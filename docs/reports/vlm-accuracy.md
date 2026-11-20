# VLM accuracy (T7)

`scripts/vlm_accuracy.py` over the 81 drawings in `scripts/drawings.py`, run 2026-11-18 with `google/gemini-3.8-flash` (fallbacks as in `config/vlm.yaml`), prompt v2. Cost about $0.24.

- **Top-1: 77 / 81 = 95%** (target 80%). Counting a fair second reading (a dog as a predator, a boat as water): 78 / 81 = 96%.
- Top-3 contains the expected type: 66 / 81 = 81%.
- Handwriting hints (RU and EN words on vague shapes): 14 / 14 right.
- Sent to the chooser (confidence under 0.6): 0; errors: 0 (none).
- Latency: median 5.7 s, max 30.1 s.

| type | right | drawings |
|---|---|---|
| aquatic | 6 / 6 | fish, whale, jellyfish, goldfish in a bowl, octopus, oval + рыба |
| creature | 8 / 8 | cow, dog, cat, bird, bee, sheep, pig, blob + корова |
| doodle | 7 / 7 | heart, star, smiley, spiral, scribble, letters ABC, word privet |
| explosive | 6 / 7 | bomb, dynamite, grenade, TNT box, mine, circle + бомба, circle + bomb |
| fire | 6 / 6 | campfire, torch, wildfire, candle, flames + огонь, sticks + campfire |
| food | 4 / 5 | apple, bread, drumstick, cheese, banana |
| house | 6 / 6 | house, hut, nest, barn, tent, square + дом |
| person | 5 / 5 | villager, warrior, fisherman, medic, girl |
| plant | 5 / 5 | apple tree, bush, flower, grass, cactus |
| predator | 6 / 7 | wolf, spider, dragon, monster, bear, blob + волк, blob + wolf |
| remote | 4 / 4 | remote control, joystick, gamepad, steering wheel |
| sky | 4 / 4 | cloud, sun, rain cloud, sun behind cloud |
| vehicle | 6 / 6 | car, boat, plane, truck, bicycle, rectangle + car |
| water | 4 / 5 | lake, river, puddle, pond with lily, puddle + lake |

Misses (expected → got, the VLM's label, confidence):

- wolf: predator → creature, "dog", 0.95 (a fair reading)
- drumstick: food → doodle, "doodle", 0.95
- pond with lily: water → doodle, "doodle", 0.95
- mine: explosive → doodle, "drawing", 0.95
