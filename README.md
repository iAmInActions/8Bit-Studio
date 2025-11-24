# 🎹 8-Bit Studio - MIDI to Chiptune Converter

![Python](https://img.shields.io/badge/Made%20with-Python-blue)
![License](https://img.shields.io/badge/License-MIT-green)

A powerful Python synthesizer that converts standard MIDI files into authentic 8-bit / chiptune music in real-time. Inspired by classic consoles like the NES and Gameboy.

Features: Live Playback, Real-time Synthesis, and **WAV Export**!

## ✨ Features

*   **Real-time Synthesis:** No samples used; all audio is mathematically generated on the fly.
*   **Smart Drum System:** Automatically detects the drum track (Channel 10).
    *   **Kick Drum:** Customizable types (Sine, Triangle, Pulse, Noise) + Decay Control.
    *   **Snare Drum:** Hybrid synthesis (Noise + Tonal Body) for a punchy sound.
*   **Melody Customization:**
    *   Waveforms: Pulse, Triangle, Sawtooth.
    *   **Pulse Width:** Adjust the duty cycle for that classic "nasal" NES sound.
    *   **Bitcrusher:** Simulate vintage sound chips by reducing the bit depth.
*   **WAV Export:** Renders the song faster than real-time into a high-quality WAV file.

## 🛠 Installation

1. **Install Python:** Make sure you have Python 3.11 or newer installed.
2. **Clone the repository:**
   ```bash
   git clone https://github.com/cosyfluf/8BIT-studio.git
   cd 8BIT-studio