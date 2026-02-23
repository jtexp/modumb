"""High-level modem interface.

Combines AFSK modulation/demodulation with audio I/O
to provide a simple byte-oriented interface.
"""

import os
import threading
import time
from typing import Optional, Callable, TYPE_CHECKING
import numpy as np

from .afsk import AFSKModulator, AFSKDemodulator, SAMPLE_RATE, BAUD_RATE, SAMPLES_PER_BIT
from .audio_io import AudioInterface, LoopbackAudioInterface

if TYPE_CHECKING:
    from .profiles import AudioProfile


# Timing constants
TURNAROUND_DELAY = 0.05  # 50ms delay for half-duplex turnaround

# Noise probe signal threshold: if the 1024-sample noise probe has
# RMS above this, it likely captured AFSK signal rather than ambient
# noise.  The samples are put back so receive_until_silence() can
# use them (otherwise the frame preamble is lost).
NOISE_SIGNAL_THRESHOLD = 0.05


class Modem:
    """High-level modem interface for sending and receiving bytes."""

    def __init__(
        self,
        audio: Optional[AudioInterface] = None,
        sample_rate: int = SAMPLE_RATE,
        baud_rate: int = BAUD_RATE,
        loopback: bool = False,
        audible: bool = False,
        input_device: Optional[int] = None,
        output_device: Optional[int] = None,
        tx_volume: Optional[float] = None,
        profile: Optional["AudioProfile"] = None,
        full_duplex: bool = False,
    ):
        """Initialize modem.

        Args:
            audio: Audio interface to use, or None to create one
            sample_rate: Audio sample rate in Hz
            baud_rate: Data rate in bits per second
            loopback: If True, use loopback audio for testing
            audible: If True, play audio even in loopback mode (demo)
            input_device: Input device index (microphone)
            output_device: Output device index (speaker)
            tx_volume: Transmit volume (0.0-1.0), overrides profile
            profile: Audio profile with transmission parameters

        Environment variables (if devices not specified):
            MODEM_INPUT_DEVICE: Input device index
            MODEM_OUTPUT_DEVICE: Output device index
            MODEM_LOOPBACK: Enable loopback mode (1/true/yes)
            MODEM_AUDIBLE: Play audio in loopback mode (1/true/yes)
            MODEM_TX_VOLUME: Transmit volume 0.0-1.0
        """
        # Baud rate priority: explicit arg > env var > default
        env_baud = os.environ.get('MODEM_BAUD_RATE')
        if baud_rate != BAUD_RATE:
            # Explicit non-default arg takes priority
            self.baud_rate = baud_rate
        elif env_baud:
            self.baud_rate = int(env_baud)
        else:
            self.baud_rate = baud_rate
        baud_rate = self.baud_rate
        self.profile = profile

        # TX volume priority: explicit arg > env var > profile > default
        if tx_volume is not None:
            self.tx_volume = tx_volume
        else:
            env_vol = os.environ.get('MODEM_TX_VOLUME')
            if env_vol:
                self.tx_volume = float(env_vol)
            elif profile:
                self.tx_volume = profile.tx_volume
            else:
                self.tx_volume = 0.08

        # Silence durations from profile
        self._lead_silence = profile.lead_silence if profile else 0.3
        self._trail_silence = profile.trail_silence if profile else 0.2

        self.full_duplex = full_duplex

        # Create audio interface if not provided
        if audio is None:
            audio_kwargs = dict(
                sample_rate=sample_rate,
                loopback=loopback,
                audible=audible,
                input_device=input_device,
                output_device=output_device,
                full_duplex=full_duplex,
            )
            if profile:
                audio_kwargs['echo_guard_time'] = profile.echo_guard_time
                audio_kwargs['hdmi_wake_enabled'] = profile.hdmi_wake_enabled
            audio = AudioInterface(**audio_kwargs)
        self.audio = audio

        # Use the audio interface's actual sample rate (may differ from requested
        # if the device doesn't support the requested rate and can't resample)
        self.sample_rate = audio.sample_rate

        # Create modulator and demodulator with the actual device sample rate
        self.modulator = AFSKModulator(
            sample_rate=self.sample_rate,
            baud_rate=baud_rate,
        )
        self.demodulator = AFSKDemodulator(
            sample_rate=self.sample_rate,
            baud_rate=baud_rate,
        )

        # State — in full-duplex mode, TX and RX are independent so no lock needed
        if full_duplex:
            class _NoOpLock:
                def __enter__(self): return self
                def __exit__(self, *args): pass
            self._lock = _NoOpLock()
        else:
            self._lock = threading.Lock()
        self._rx_callback: Optional[Callable[[bytes], None]] = None
        self._last_rx_samples: Optional[np.ndarray] = None  # for WAV dump on decode failure

    def start(self) -> None:
        """Start the modem."""
        self.audio.start()

    def stop(self) -> None:
        """Stop the modem."""
        self.audio.stop()

    def send(self, data: bytes, blocking: bool = True) -> None:
        """Send data over the modem.

        Args:
            data: Bytes to send
            blocking: If True, wait until transmission complete
        """
        import sys
        with self._lock:
            tx_start = time.monotonic()

            # Modulate data to audio samples
            samples = self.modulator.modulate(data)

            # Apply TX volume to avoid clipping
            if self.tx_volume < 1.0:
                samples = samples * self.tx_volume

            # Leading silence lets the acoustic channel and demodulator filters settle.
            # Trailing silence ensures the receiver detects end-of-frame silence.
            lead_silence = np.zeros(int(self._lead_silence * self.sample_rate), dtype=np.float32)
            trail_silence = np.zeros(int(self._trail_silence * self.sample_rate), dtype=np.float32)
            samples = np.concatenate([lead_silence, samples, trail_silence])

            # Transmit
            self.audio.transmit(samples, blocking=blocking)

            tx_elapsed = (time.monotonic() - tx_start) * 1000
            print(f'MODEM TX: {len(data)}B tx_time={tx_elapsed:.0f}ms',
                  file=sys.stderr, flush=True)

            if blocking and not self.full_duplex:
                # Half-duplex turnaround delay
                time.sleep(TURNAROUND_DELAY)

    def receive(self, timeout: float = 5.0) -> bytes:
        """Receive data from the modem.

        Args:
            timeout: Maximum time to wait for data

        Returns:
            Received bytes, or empty bytes on timeout
        """
        with self._lock:
            import sys
            rx_start = time.monotonic()

            # Drain any stale audio that accumulated in the receive queue
            # while we weren't actively listening (not needed in full-duplex
            # where RX runs independently of TX).
            if not self.full_duplex:
                self.audio.clear_receive_buffer()

            # Measure ambient noise level from a short sample to set
            # adaptive silence threshold (UMIK-1 measurement mics have
            # higher noise floors than typical mics)
            noise_sample = self.audio.receive(1024, timeout=0.1)
            noise_rms = float(np.sqrt(np.mean(noise_sample ** 2))) if len(noise_sample) > 0 else 0.01
            # Cap threshold: if the noise probe captures AFSK signal
            # (race between clear_receive_buffer and incoming frame),
            # noise_rms can spike above 0.2, making the threshold so
            # high that receive_until_silence never detects signal.
            silence_threshold = max(0.01, min(0.05, noise_rms * 3))

            # Always put noise-probe samples back so
            # receive_until_silence() gets the complete capture.
            # Previously we only re-queued when noise_rms exceeded the
            # signal threshold, but in full-duplex the probe can land on
            # lead silence of an incoming frame — discarding those samples
            # shifts the demodulator's view and corrupts the preamble.
            if len(noise_sample) > 0:
                self.audio._rx_queue.put(noise_sample)

            samples = self.audio.receive_until_silence(
                timeout=timeout,
                threshold=silence_threshold,
                min_samples=10000,  # ~200ms of audio minimum
                silence_duration=max(0.1, 0.3 * 300 / self.baud_rate),
            )

            if len(samples) == 0:
                return b''

            signal_ms = (time.monotonic() - rx_start) * 1000
            duration_ms = len(samples) / self.sample_rate * 1000
            print(f'MODEM RX: noise_rms={noise_rms:.4f} threshold={silence_threshold:.4f} '
                  f'captured={len(samples)} samples ({duration_ms:.0f}ms) '
                  f'wait={signal_ms:.0f}ms',
                  file=sys.stderr, flush=True)

            # Store raw samples for WAV dump on decode failure
            self._last_rx_samples = samples.copy()

            # Trim leading silence before demodulation.
            # receive_until_silence() collects all audio blocks including
            # pre-signal silence, which dilutes the demodulator's RMS
            # normalization and can misguide clock recovery.
            samples = self._trim_leading_silence(samples)

            # Demodulate to bytes
            data = self.demodulator.demodulate(samples)

            print(f'MODEM RX: demodulated={len(data)} bytes',
                  file=sys.stderr, flush=True)

            return data

    def _trim_leading_silence(self, samples: np.ndarray) -> np.ndarray:
        """Trim leading silence, keeping margin before signal for filter settling.

        receive_until_silence() often returns seconds of silence before
        the actual AFSK signal. This silence degrades the demodulator's
        RMS normalization (used to equalize mark/space frequency response)
        and pollutes clock recovery envelope crossings.
        """
        spb = self.demodulator.samples_per_bit
        if len(samples) < spb * 16:
            return samples  # Too short to trim

        abs_samples = np.abs(samples)
        max_amp = float(np.max(abs_samples))
        if max_amp < 0.005:
            return samples  # No signal detected

        # Find first sample above 10% of max amplitude
        threshold = max_amp * 0.1
        above = np.where(abs_samples > threshold)[0]
        if len(above) == 0:
            return samples

        # Keep margin before signal start for bandpass filter settling.
        # 16 bit periods (13.3ms@1200, 53ms@300) absorbs timing jitter
        # from concurrent full-duplex I/O that can corrupt the preamble.
        margin = spb * 16  # 16 bit periods
        start = max(0, int(above[0]) - margin)

        return samples[start:]

    def receive_bytes(self, num_bytes: int, timeout: float = 5.0) -> bytes:
        """Receive a specific number of bytes.

        Args:
            num_bytes: Number of bytes to receive
            timeout: Maximum time to wait

        Returns:
            Received bytes (may be less than requested on timeout)
        """
        # Calculate expected number of samples
        bits_needed = num_bytes * 8
        samples_needed = bits_needed * (self.sample_rate // self.baud_rate)
        # Add margin for preamble detection
        samples_needed = int(samples_needed * 1.5)

        with self._lock:
            samples = self.audio.receive(samples_needed, timeout=timeout)

            if len(samples) == 0:
                return b''

            data = self.demodulator.demodulate(samples)
            return data[:num_bytes]

    def set_receive_callback(self, callback: Optional[Callable[[bytes], None]]) -> None:
        """Set callback for received data.

        Args:
            callback: Function to call with received data, or None to disable
        """
        self._rx_callback = callback

    @property
    def bytes_per_second(self) -> float:
        """Return the data rate in bytes per second."""
        return self.baud_rate / 8

    @property
    def is_running(self) -> bool:
        """Check if modem is running."""
        return self.audio.is_running

    def __enter__(self) -> "Modem":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


class LoopbackModem(Modem):
    """Modem with internal loopback for testing."""

    def __init__(self, sample_rate: int = SAMPLE_RATE, baud_rate: int = BAUD_RATE):
        super().__init__(sample_rate=sample_rate, baud_rate=baud_rate, loopback=True)
