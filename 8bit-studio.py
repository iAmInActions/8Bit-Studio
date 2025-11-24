import tkinter as tk
from tkinter import filedialog, ttk, messagebox
import mido
import numpy as np
import sounddevice as sd
import threading
import wave
import traceback
import time

class RetroSynth:
    def __init__(self):
        self.sample_rate = 44100
        self.max_polyphony = 16 # Mehr Stimmen für Sicherheit
        self.active_notes = {} 
        self.lock = threading.Lock()
        
        # --- GLOBAL ---
        self.drum_channel = 9 
        self.bit_depth = 16.0 

        # --- SETTINGS ---
        self.melody_vol = 0.5
        self.waveform = "Pulse" 
        self.pulse_width = 0.5 

        self.kick_vol = 1.0
        self.kick_decay = 0.15 
        self.kick_type = "Triangle" 

        self.snare_vol = 0.8
        self.snare_decay = 0.2 
        self.snare_body = 0.5  
        self.snare_type = "White Noise"

        self.current_sample_index = 0
        self.stream = None

    def get_freq(self, midi_note):
        return 440.0 * (2.0 ** ((midi_note - 69) / 12.0))

    def reset_state(self):
        with self.lock:
            self.active_notes.clear()
            self.current_sample_index = 0

    def generate_chunk(self, frames, current_time_index):
        if frames <= 0: return np.array([])

        global_t = (np.arange(frames) + current_time_index) / self.sample_rate
        mix = np.zeros(frames)
        
        # Polyphonie Limitierung (Safe Copy)
        if len(self.active_notes) > self.max_polyphony:
            try:
                # Wir sortieren eine Kopie, um Thread-Crashs zu vermeiden
                current_notes = list(self.active_notes.items())
                sorted_notes = sorted(current_notes, key=lambda item: item[1]['start_time'], reverse=True)
                with self.lock:
                    self.active_notes = dict(sorted_notes[:self.max_polyphony])
            except: pass

        # WICHTIG: Note Count merken für Normalisierung
        note_count = len(self.active_notes)
        
        # SUPER WICHTIG: Iteration über eine Kopie der Liste
        # Das verhindert "RuntimeError: dictionary changed size"
        safe_notes_list = list(self.active_notes.items())

        for note, data in safe_notes_list:
            freq = data['freq']
            sound_type = data['type']
            start_sample = data['start_time']
            
            # Zeit relativ zum Start der Note
            note_t = global_t - (start_sample / self.sample_rate)
            
            # Negative Zeiten (Note startet mitten im Chunk) abfangen
            # np.maximum verhindert NaN oder Fehler bei exp
            note_t = np.maximum(0, note_t)

            wave_data = np.zeros(frames)
            
            # --- KICK ---
            if sound_type == 'kick':
                env = np.exp(-note_t * (1.0 / max(0.01, self.kick_decay)))
                phase = (global_t * freq) % 1.0
                
                if self.kick_type == "Triangle":
                    raw = 2.0 * np.abs(2.0 * (phase - np.floor(phase + 0.5))) - 1.0
                elif self.kick_type == "Sine":
                    raw = np.sin(2 * np.pi * freq * global_t)
                elif self.kick_type == "Pulse":
                    raw = np.sign(np.sin(2 * np.pi * freq * global_t))
                elif self.kick_type == "Noise":
                    raw = np.random.uniform(-1, 1, frames)
                else:
                    raw = np.zeros(frames)
                wave_data = raw * env * self.kick_vol * 2.0 

            # --- SNARE ---
            elif sound_type == 'snare':
                env_noise = np.exp(-note_t * (1.0 / max(0.01, self.snare_decay)))
                
                if self.snare_type == "White Noise":
                    noise = np.random.uniform(-1, 1, frames)
                elif self.snare_type == "Digital":
                    noise = np.random.choice([-1, 1], size=frames)
                else: 
                    mod = np.sin(2 * np.pi * (freq * 4.5) * global_t)
                    noise = np.random.uniform(-1, 1, frames) * mod
                
                noise_part = noise * env_noise
                
                # Body/Punch
                body_freq = 180.0 
                env_body = np.exp(-note_t * 15.0) 
                body_part = np.sin(2 * np.pi * body_freq * global_t) * env_body

                wave_data = (noise_part * (1.0 - (self.snare_body * 0.4))) + (body_part * self.snare_body * 2.0)
                wave_data *= self.snare_vol

            # --- MELODY ---
            else:
                phase = (global_t * freq) % 1.0
                if self.waveform == "Pulse":
                    wave_data = np.where(phase < self.pulse_width, 1.0, -1.0)
                elif self.waveform == "Triangle":
                    wave_data = 2.0 * np.abs(2.0 * (phase - np.floor(phase + 0.5))) - 1.0
                elif self.waveform == "Sawtooth":
                    wave_data = 2.0 * (phase - 0.5)
                wave_data *= self.melody_vol

            mix += wave_data * data['vel']

        # Normalisierung
        if note_count > 0:
            mix = mix / (note_count ** 0.55)

        # Bitcrusher
        if self.bit_depth < 128:
            mix = np.round(mix * self.bit_depth) / self.bit_depth

        return mix * 0.5

    def audio_callback(self, outdata, frames, time_info, status):
        if status: print(status)
        try:
            # Live Playback ist fehlertolerant
            mix = self.generate_chunk(frames, self.current_sample_index)
            self.current_sample_index += frames
            outdata[:] = mix.reshape(-1, 1)
        except Exception:
            # Im Zweifel Stille ausgeben statt abstürzen
            outdata[:] = np.zeros((frames, 1))

    def start_stream(self):
        self.stop_stream()
        self.stream = sd.OutputStream(channels=1, samplerate=self.sample_rate, callback=self.audio_callback)
        self.stream.start()

    def stop_stream(self):
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except: pass
            self.stream = None

    def note_on(self, note, velocity, channel):
        vol = velocity / 127.0
        if channel == self.drum_channel: 
            if note < 38: s_type = 'kick' 
            else: s_type = 'snare' 
        else:
            s_type = 'melody'

        with self.lock:
            self.active_notes[note] = {
                'freq': self.get_freq(note), 
                'vel': vol,
                'start_time': self.current_sample_index,
                'type': s_type
            }

    def note_off(self, note):
        with self.lock:
            if note in self.active_notes:
                # Drums lassen wir ausklingen (Envelope), Melodie löschen wir
                if self.active_notes[note]['type'] == 'melody':
                    del self.active_notes[note]
    
    def all_notes_off(self):
        with self.lock:
            self.active_notes.clear()


class RetroMidiApp:
    def __init__(self, root):
        self.root = root
        self.root.title("8-BIT STUDIO: FINAL FIX")
        self.root.geometry("600x680")
        
        self.bg = "#1e1e1e" 
        self.fg = "#00ffcc" 
        self.acc = "#ff0099" 
        self.root.configure(bg=self.bg)
        
        self.style = ttk.Style()
        self.style.theme_use('clam')
        self.style.configure("TFrame", background=self.bg)
        self.style.configure("TLabel", background=self.bg, foreground="white", font=("Verdana", 9))
        self.style.configure("TLabelframe", background=self.bg, foreground=self.fg, bordercolor="#444")
        self.style.configure("TLabelframe.Label", background=self.bg, foreground=self.fg, font=("Verdana", 10, "bold"))
        self.style.configure("TButton", background="#333", foreground="white", bordercolor="#555", font=("Verdana", 9, "bold"))
        self.style.map("TButton", background=[("active", self.acc)], foreground=[("active", "white")])

        self.synth = RetroSynth()
        self.midi_file = None
        self.is_playing = False
        self.total_messages = 0
        
        self.setup_ui()

    def setup_ui(self):
        main = ttk.Frame(self.root, padding=15)
        main.pack(fill=tk.BOTH, expand=True)

        top_frame = ttk.Frame(main)
        top_frame.pack(fill=tk.X, pady=(0,10))
        ttk.Label(top_frame, text="8-BIT STUDIO", font=("Impact", 20), foreground=self.acc).pack(side=tk.LEFT)
        self.lbl_status = ttk.Label(top_frame, text="READY", foreground="#888")
        self.lbl_status.pack(side=tk.RIGHT, padx=10)

        ctrl_frame = ttk.LabelFrame(main, text=" CONTROLS ", padding=10)
        ctrl_frame.pack(fill=tk.X, pady=5)
        
        self.lbl_file = ttk.Label(ctrl_frame, text="NO FILE LOADED", width=40, anchor="center")
        self.lbl_file.pack(pady=5)
        
        btn_box = ttk.Frame(ctrl_frame)
        btn_box.pack()
        ttk.Button(btn_box, text="LOAD MIDI", command=self.load_midi).pack(side=tk.LEFT, padx=5)
        self.btn_play = ttk.Button(btn_box, text="PLAY", command=self.toggle_play, state=tk.NORMAL)
        self.btn_play.pack(side=tk.LEFT, padx=5)
        self.btn_export = ttk.Button(btn_box, text="EXPORT WAV", command=self.export_wav, state=tk.NORMAL)
        self.btn_export.pack(side=tk.LEFT, padx=5)

        mix_frame = ttk.LabelFrame(main, text=" MIXER ", padding=10)
        mix_frame.pack(fill=tk.X, pady=5)
        ttk.Label(mix_frame, text="Drum CH:").grid(row=0, column=0)
        self.spin_ch = tk.Spinbox(mix_frame, from_=1, to=16, width=5, command=lambda: setattr(self.synth, 'drum_channel', int(self.spin_ch.get())-1))
        self.spin_ch.delete(0, "end"); self.spin_ch.insert(0, "10")
        self.spin_ch.grid(row=0, column=1, padx=5)
        
        ttk.Label(mix_frame, text="Bit Crush:").grid(row=0, column=2)
        s_bit = ttk.Scale(mix_frame, from_=2, to=64, command=lambda v: setattr(self.synth, 'bit_depth', float(v)))
        s_bit.set(16); s_bit.grid(row=0, column=3, sticky="ew")

        inst_frame = ttk.Frame(main)
        inst_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        # MELODY
        f_mel = ttk.LabelFrame(inst_frame, text=" MELODY ", padding=5)
        f_mel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2)
        self.create_slider(f_mel, "Volume", 0, 1.0, 0.5, lambda v: setattr(self.synth, 'melody_vol', float(v)))
        self.create_combo(f_mel, "Wave", ["Pulse", "Triangle", "Sawtooth"], "Pulse", lambda e,v: setattr(self.synth, 'waveform', v.get()))
        self.create_slider(f_mel, "Width", 0.01, 0.5, 0.25, lambda v: setattr(self.synth, 'pulse_width', float(v)))

        # KICK
        f_kick = ttk.LabelFrame(inst_frame, text=" KICK ", padding=5)
        f_kick.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2)
        self.create_slider(f_kick, "Volume", 0, 2.0, 1.0, lambda v: setattr(self.synth, 'kick_vol', float(v)))
        self.create_combo(f_kick, "Type", ["Triangle", "Sine", "Pulse", "Noise"], "Triangle", lambda e,v: setattr(self.synth, 'kick_type', v.get()))
        self.create_slider(f_kick, "Decay", 0.05, 1.0, 0.15, lambda v: setattr(self.synth, 'kick_decay', float(v)))

        # SNARE
        f_snare = ttk.LabelFrame(inst_frame, text=" SNARE ", padding=5)
        f_snare.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2)
        self.create_slider(f_snare, "Volume", 0, 2.0, 0.8, lambda v: setattr(self.synth, 'snare_vol', float(v)))
        self.create_combo(f_snare, "Type", ["White Noise", "Digital", "Metal"], "White Noise", lambda e,v: setattr(self.synth, 'snare_type', v.get()))
        self.create_slider(f_snare, "Body/Punch", 0.0, 1.0, 0.5, lambda v: setattr(self.synth, 'snare_body', float(v)))

    def create_slider(self, parent, label, min_v, max_v, default, cmd):
        ttk.Label(parent, text=label).pack(anchor="w")
        s = ttk.Scale(parent, from_=min_v, to=max_v, command=cmd); s.set(default); s.pack(fill=tk.X)
    def create_combo(self, parent, label, values, default, callback):
        ttk.Label(parent, text=label).pack(anchor="w")
        var = tk.StringVar(value=default)
        cb = ttk.Combobox(parent, textvariable=var, values=values, state="readonly"); cb.pack(fill=tk.X)
        cb.bind("<<ComboboxSelected>>", lambda e: callback(e, var))

    def load_midi(self):
        path = filedialog.askopenfilename(filetypes=[("MIDI", "*.mid"), ("All Files", "*.*")])
        if path:
            try:
                self.midi_file = mido.MidiFile(path)
                self.lbl_file.config(text=path.split('/')[-1])
                self.lbl_status.config(text="FILE OK.", foreground="#0f0")
                # Zähle Nachrichten für Progress Bar Berechnung
                self.total_messages = sum(1 for _ in self.midi_file)
            except Exception as e:
                traceback.print_exc()
                messagebox.showerror("Error", str(e))

    def toggle_play(self):
        if not self.midi_file: return
        if self.is_playing:
            self.stop_internal()
        else:
            self.is_playing = True
            self.synth.reset_state()
            self.synth.start_stream()
            threading.Thread(target=self.play_thread, daemon=True).start()
            self.btn_play.config(text="STOP")
            self.lbl_status.config(text="PLAYING...", foreground=self.fg)

    def play_thread(self):
        try:
            for msg in self.midi_file.play():
                if not self.is_playing: break
                ch = getattr(msg, 'channel', -1)
                
                if msg.type == 'note_on' and msg.velocity > 0:
                    self.synth.note_on(msg.note, msg.velocity, ch)
                elif msg.type == 'note_off' or (msg.type=='note_on' and msg.velocity==0):
                    self.synth.note_off(msg.note)
            self.root.after(0, lambda: self.stop_internal())
        except Exception as e: print(e)

    def stop_internal(self):
        self.is_playing = False
        self.synth.stop_stream()
        self.btn_play.config(text="PLAY")
        self.lbl_status.config(text="STOPPED")

    def export_wav(self):
        if not self.midi_file:
            messagebox.showwarning("Info", "Keine Datei geladen.")
            return
        
        if self.is_playing:
            self.stop_internal()

        path = filedialog.asksaveasfilename(defaultextension=".wav", filetypes=[("WAV", "*.wav")])
        if not path: return
        
        self.lbl_status.config(text="STARTING...", foreground=self.acc)
        self.root.update()
        threading.Thread(target=self.render_thread, args=(path,)).start()

    def render_thread(self, filename):
        try:
            self.synth.stop_stream()
            self.synth.reset_state()
            
            buffer = []
            print(f"Starte Export nach: {filename}")
            
            # METHODE 2: Über die Datei iterieren (Sicherste Methode für Zeit)
            # Mido wandelt 'time' hier automatisch in Sekunden um (Delta)
            
            count = 0
            # Wir iterieren über das File Objekt, nicht über tracks
            # Das simuliert play() ohne warten
            
            for msg in self.midi_file:
                count += 1
                if count % 200 == 0:
                     # GUI Update (Thread Safe Wrapper)
                     p = (count / self.total_messages) * 100
                     print(f"Export: {int(p)}%...")
                     self.root.after(0, lambda txt=f"EXP: {int(p)}%": self.lbl_status.config(text=txt))

                # Delta berechnen (msg.time ist Sekunden bei iterieren über MidiFile)
                delta = int(msg.time * self.synth.sample_rate)
                
                if delta > 0:
                    chunk = self.synth.generate_chunk(delta, self.synth.current_sample_index)
                    if len(chunk) > 0:
                        buffer.append(chunk)
                        self.synth.current_sample_index += delta
                
                ch = getattr(msg, 'channel', -1)
                
                # Try-Except um defekte MIDI Events zu ignorieren
                try:
                    if msg.type == 'note_on':
                        if msg.velocity > 0: 
                            self.synth.note_on(msg.note, msg.velocity, ch)
                        else: 
                            self.synth.note_off(msg.note)
                    elif msg.type == 'note_off': 
                        self.synth.note_off(msg.note)
                except Exception as e:
                    pass # Fehler bei einzelner Note ignorieren

            # Letzter Ausklang
            print("Rendering Ausklang...")
            buffer.append(self.synth.generate_chunk(int(self.synth.sample_rate), self.synth.current_sample_index))
            
            if len(buffer) == 0:
                raise Exception("Keine Audio-Daten generiert! Ist die MIDI Datei leer?")

            print("Zusammenfügen...")
            full = np.concatenate(buffer)
            
            print("Normalisieren...")
            m = np.max(np.abs(full))
            if m > 0: full = full/m * 0.95
            
            print("Speichern...")
            with wave.open(filename, 'w') as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(self.synth.sample_rate)
                f.writeframes((full * 32767).astype(np.int16).tobytes())
            
            print("Fertig!")
            self.root.after(0, lambda f=filename: messagebox.showinfo("Success", f"Gespeichert: {f}"))
            self.root.after(0, lambda: self.lbl_status.config(text="DONE", foreground="#888"))

        except Exception as e:
            print(f"Export Critical Error: {e}")
            traceback.print_exc()
            err_msg = str(e)
            self.root.after(0, lambda err=err_msg: messagebox.showerror("Export Failed", err))
            self.root.after(0, lambda: self.lbl_status.config(text="ERROR", foreground="red"))

if __name__ == "__main__":
    root = tk.Tk()
    app = RetroMidiApp(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.synth.stop_stream(), root.destroy()))
    root.mainloop()