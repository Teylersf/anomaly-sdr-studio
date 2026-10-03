# Recorded I/Q captures

Replay reads app-listed files in `%LOCALAPPDATA%\AnomalySDRStudio\captures`, or `captures` inside the folder selected with `--data-dir`. It does not accept arbitrary file paths from a browser request.

The first version accepts files with these properties:

- Extension `.iq`, with a JSON sidecar sharing the same base filename.
- Signed 8-bit interleaved I and Q, one byte each, without a header.
- Exactly 8,000,000 complex samples per second.
- An even byte count from 2 bytes through 256 MiB.
- Sidecar byte count equal to the actual file size.
- Center frequency from 1 MHz through 6 GHz.

For example, a one-second capture contains 16,000,000 bytes. If its filename is `example.iq`, the accompanying `example.json` can be:

```json
{
  "sampleRateHz": 8000000,
  "frequencyHz": 433920000,
  "bytes": 16000000,
  "format": "signed 8-bit interleaved I,Q; no header",
  "startedUtc": "2026-01-01T00:00:00.000Z"
}
```

Use the actual frequency, size, and recording time for your file. The `format` string must match exactly. Unsigned bytes, float I/Q, WAV, and a different sample rate cannot be made compatible by merely changing the sidecar; the samples must first be converted correctly.

The app lists up to 30 compatible captures. Select a listed capture and start replay. Its axes use the recorded center frequency and sample rate. The source label identifies recorded data, and replay samples are not live reception.

Replay repeats by default. Each end-to-start boundary becomes a gap and clears pending frame-decoder samples. The analysis does not join the last bytes of a recording to its first bytes to manufacture a complete packet or a continuous rhythm. Replaying the same file repeatedly is not independent evidence of a signal recurring on the air.

Recorded radio samples and metadata may contain received content and local information. Review files before sharing them.
