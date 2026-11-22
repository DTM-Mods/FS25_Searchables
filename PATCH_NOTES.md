<p align="center">
  <img src="https://raw.githubusercontent.com/DTM-Mods/.github/main/assets/patch-notes.jpg" alt="Patch notes" width="100%">
</p>

## 1.8.2.8

- Fix: model names such as T7, 6M or L 538 are found by name. The short units t, l and m only act as weight, capacity and width filters with a comparison or a range (`t>7`, `l<700`, `6-9m`); `12m`, `10t` and `30000l` on their own are now search words (use `width=12`, `t=10`, `l=30000`).
- Fix: typing a number on the map finds the field, property, model or license plate with that number, and no longer things that merely show it in their label (fuel %, litres, price, hectares, animal count, contract reward).
- Numbers can be typed with a decimal comma: `width>4,5` means 4.5 and `$<1,5k` means 1,500. A comma before exactly three digits is still a thousands separator (`$144,500`).
- Fix: a search box that opens with a search already in it (the map box showing your last search) shows only the X, not the pen underneath it.
- Offline tests (`tests/test_offline.py`, 33 tests), run by every build.

## 1.8.2.7

- New title: Searchables. The description is now in all 27 game languages.
- Description in GIANTS' ModHub layout (category, price, upkeep or hotkeys at the top).
- New mod icon in the ModHub standard.

## 1.8.2.6

- Updated for the current game version: modDesc descVersion 116 (the game build of 2026-10-27; GIANTS TestRunner 0.9.24 accepts only 116).

## 1.8.2.5

- Passes the GIANTS ModHub TestRunner: errors are no longer hidden (they show in the log), the icon and map markers are compressed textures, and the mod list icon is a .dds.

## 1.8.2.4

- Searchables is now **DTM Searchables**, in `FS25_DTMSearchables.zip` (ModHub already had a mod by the old file name). Remove the old `FS25_Searchables.zip` from your mods folder so the two don't run side by side. Your settings carry over, and it never saved anything to your savegame. Nothing else changed.

## 1.8.2.3

- The author now shows as **DTM Mods, Fennell** in the mod list.

## 1.8.2.2

- A search with a field state ("wheat ready", "growing", "withered", "harvested") only looks at fields. On a map without wheat fields, "wheat ready" listed production points whose product lists happened to hold both words.

## 1.8.2.1

- New mod icon in the DTM Mods style. Nothing else changed.
