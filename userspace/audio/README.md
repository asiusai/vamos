# Dragon/Panda audio topology

`QCS6490-Asius-Panda.m4` is the minimal AudioReach graph for the Primary MI2S
connection between Dragon Q6A and panda-v5. It retains only 48 kHz S16_LE
MultiMedia1 playback on Q6 DAI 16 and MultiMedia3 capture on Q6 DAI 17.

The source is derived from the upstream BSD-3-Clause
`QCS6490-Thundercomm-RubikPi3.m4` topology. Regenerate and checksum-verify the
tracked firmware with:

```sh
./userspace/audio/build_topology.sh
```
