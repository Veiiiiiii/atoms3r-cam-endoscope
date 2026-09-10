# Third-party notices

The firmware tree is derived from M5Stack's `AtomS3R-CAM-UserDemo` at commit
`8effad452356b276d9732ead4740c38df1d2af2e`. Individual source files retain
their M5Stack, Espressif, TinyUSB and other upstream copyright/licence headers.

Fetched build dependencies and their exact branches/tags are declared in
`firmware/repos.json`. Their own licence files govern those components. They
are intentionally omitted from the compact release archive and downloaded by
`firmware/fetch_repos.py` during a source build.

The direct TinyUSB source is pinned to Espressif commit
`33d36cb23d6f8415d0e8a09d15a5e6cd8c0a6d05`, corresponding to component
registry release `espressif/tinyusb` `0.15.0~10`.

New project-specific source and scripts are provided under the MIT licence.
