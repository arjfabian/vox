"""
tools/enroll_speaker.py

One-time setup: records voice samples from the operator and generates a speaker
embedding saved to identity/master_voice.npy.

Run this before starting VOX to support voice command recognition:

    python tools/enroll_speaker.py

Requirements:
    pip install resemblyzer sounddevice soundfile numpy

The identity/ directory is gitignored. Voice data never leaves the machine.
"""

import sys
import time
from pathlib import Path

import numpy as np

# ------------------------------------------------------------------------------
# Config
# ------------------------------------------------------------------------------

IDENTITY_DIR = Path("identity")
OUTPUT_PATH = IDENTITY_DIR / "master_voice.npy"
SAMPLE_RATE = 16000  # Hz — Whisper and resemblyzer both expect 16kHz
RECORD_SECS = 5  # seconds per sample
NUM_SAMPLES = 5  # more samples → more robust embedding
SAMPLE_PAUSE = 1.5  # seconds between recordings


def _check_dependencies():
    missing = []
    for pkg in ("resemblyzer", "sounddevice", "soundfile", "numpy"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"\n[ERROR] Missing dependencies: {', '.join(missing)}")
        print(f"Install with: pip install {' '.join(missing)}")
        sys.exit(1)


def _record_sample(index: int, total: int) -> np.ndarray:
    import sounddevice as sd

    print(f"\n- Sample {index}/{total}.")
    print(f"  Speak naturally for {RECORD_SECS} seconds.")
    print("  Recording in 3...", end="", flush=True)
    time.sleep(1)
    print("  Recording in 2...", end="", flush=True)
    time.sleep(1)
    print("  Recording in 1...", end="", flush=True)
    time.sleep(1)
    print(" GO!")

    audio = sd.rec(
        int(RECORD_SECS * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
    )
    sd.wait()
    print("  Done.")
    return audio.squeeze()


def _compute_embedding(samples: list) -> np.ndarray:
    from resemblyzer import VoiceEncoder, preprocess_wav

    encoder = VoiceEncoder()
    embeddings = []

    for i, audio in enumerate(samples):
        try:
            wav = preprocess_wav(audio, source_sr=SAMPLE_RATE)
            emb = encoder.embed_utterance(wav)
            embeddings.append(emb)
            print(f"  Embedding {i + 1}/{len(samples)} computed.")
        except Exception as e:  # noqa: BLE001 — sample skip is non-fatal
            print(f"  [WARN] Sample {i + 1} skipped: {e}")

    if not embeddings:
        print("\n[ERROR] No valid embeddings generated. Check your microphone.")
        sys.exit(1)

    # Average across all samples for a robust profile
    return np.mean(embeddings, axis=0)


def main():
    print("=" * 60)
    print("  VOX — Speaker Enrollment")
    print("=" * 60)
    print("""
  This script records your voice and generates a speaker profile used by VOX to
  authenticate voice commands.

  Guidelines for best results:
    - Use the same microphone you'll use day-to-day
    - Speak at a natural pace — no need to slow down
    - Vary your sentences across samples (don't repeat the same phrase)
    - Avoid background music; ambient noise is fine
    - You can speak in any language VOX will use (ES, EN, PT)
""")

    _check_dependencies()

    # Ensure identity/ exists
    IDENTITY_DIR.mkdir(exist_ok=True)

    if OUTPUT_PATH.exists():
        print(f"  [!] Existing profile found at '{OUTPUT_PATH}'.")
        answer = input("  Overwrite? (yes/no): ").strip().lower()
        if answer not in ("yes", "y"):
            print("  Enrollment cancelled.")
            sys.exit(0)

    input("  Press ENTER when you're ready to begin...")

    samples = []
    for i in range(1, NUM_SAMPLES + 1):
        audio = _record_sample(i, NUM_SAMPLES)
        samples.append(audio)
        if i < NUM_SAMPLES:
            print(f"  Pause {SAMPLE_PAUSE}s before next sample...")
            time.sleep(SAMPLE_PAUSE)

    print(f"\n  Computing speaker embedding from {len(samples)} samples...")
    embedding = _compute_embedding(samples)

    np.save(str(OUTPUT_PATH), embedding)
    print(f"\n  Profile saved to '{OUTPUT_PATH}'.")
    print(f"  Embedding shape: {embedding.shape}, dtype: {embedding.dtype}")
    print("\n  Enrollment complete.")
    print("  VOX will recognize your voice on next boot.")
    print("=" * 60)


if __name__ == "__main__":
    main()
