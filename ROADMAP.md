# Roadmap

Ideas under consideration, not yet scheduled or committed to.

## Watch-from-start / short-term DVR

Let someone who joins a game late start from kickoff instead of the live
edge. Sketch: `ffmpeg -c copy` remuxes each live game's resolved stream to
a rolling segment window on disk (no transcoding — just a stream copy),
exposed as an extra "Watch from start" entry in the existing Sources list.
Cleaned up in the same pass that already prunes finished games after
`SUNDAYSIGNAL_KEEP_FINAL_HOURS`, so nothing sticks around more than a few
hours past the final whistle.

Open questions before building it:
- Disk/CPU scale with *concurrent* live games, not total games — a Sunday
  early slate can mean 6+ simultaneous recordings at once.
- Storing and re-serving copies of broadcast video, even briefly, is a
  more exposed posture than the app's current pure-relay/proxy model —
  worth being deliberate about before committing to it.
