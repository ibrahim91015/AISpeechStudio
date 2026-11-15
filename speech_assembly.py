import hashlib
import json
import os
import struct
import wave


class SpeechAssembly:
    """An append-only PCM file with one durable checkpoint per source chunk."""

    def __init__(self, folder, chunks):
        self.path = folder / "assembly.pcm"
        self.checkpoint = folder / "assembly.json"
        self.signature = hashlib.sha256("\0".join(chunks).encode()).hexdigest()
        self.index = self.size = 0
        self.rate = self.channels = 0
        try:
            state = json.loads(self.checkpoint.read_text(encoding="utf-8"))
            size, index, rate, channels = (state[key] for key in ("size", "index", "rate", "channels"))
            if (all(type(value) is int for value in (size, index, rate, channels)) and
                    state["signature"] == self.signature and 0 < index <= len(chunks) and
                    0 < rate <= 192000 and channels in (1, 2) and size > 0 and
                    size % (channels * 2) == 0 and self.path.stat().st_size >= size):
                self.index, self.size, self.rate, self.channels = index, size, rate, channels
        except (OSError, ValueError, TypeError, KeyError):
            pass
        self.rewind()

    def rewind(self):
        with self.path.open("ab") as pcm:
            pcm.truncate(self.size)
        if not self.size:
            self.rate = self.channels = 0

    def append(self, part, pause_ms):
        with wave.open(str(part), "rb") as source:
            rate, channels = source.getframerate(), source.getnchannels()
            if (source.getsampwidth() != 2 or channels not in (1, 2) or not source.getnframes() or not 8000 <= rate <= 192000 or
                    (self.rate and (rate, channels) != (self.rate, self.channels))):
                raise RuntimeError("A chunk’s audio format did not match the recording.")
            self.rate, self.channels = rate, channels
            with self.path.open("ab") as pcm:
                if pcm.tell():
                    frames = round(rate * pause_ms / 1000)
                    pcm.write(b"\0" * (frames * channels * 2))
                remaining = source.getnframes()
                fade = min(round(rate * .004), remaining // 2)
                total = remaining
                while remaining:
                    frames = source.readframes(min(remaining, 32_768))
                    if not frames or len(frames) % (channels * 2):
                        raise RuntimeError("The model returned a truncated audio chunk.")
                    count = len(frames) // (channels * 2)
                    start = total - remaining
                    if fade and (start < fade or remaining <= count + fade):
                        frames = soften_edges(frames, channels, start, count, total, fade)
                    pcm.write(frames)
                    remaining -= count

    def commit(self, index):
        with self.path.open("ab") as pcm:
            self.size = pcm.tell()
            os.fsync(pcm.fileno())
        state = {"index": index, "size": self.size, "rate": self.rate, "channels": self.channels,
                 "signature": self.signature}
        staged = self.checkpoint.with_suffix(".tmp")
        with staged.open("w", encoding="utf-8") as file:
            json.dump(state, file)
            file.flush()
            os.fsync(file.fileno())
        staged.replace(self.checkpoint)
        if os.name == "posix":
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        self.index = index

    @property
    def duration(self):
        return self.size / (self.rate * self.channels * 2)


def soften_edges(frames, channels, start, count, total, fade):
    samples = bytearray(frames)
    for frame in range(count):
        distance = min(start + frame, total - start - frame - 1)
        if distance < fade:
            for channel in range(channels):
                offset = 2 * (frame * channels + channel)
                sample, = struct.unpack_from("<h", samples, offset)
                struct.pack_into("<h", samples, offset, round(sample * distance / fade))
    return samples
