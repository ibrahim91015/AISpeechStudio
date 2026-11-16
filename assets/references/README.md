# Reference collections

The 73 bundled FLAC files come from public speech datasets. They were converted to mono FLAC, with selected samples trimmed or combined.

| Collection | Audio | Attribution |
| --- | ---: | --- |
| VCTK English accents | 70 bundled | Junichi Yamagishi, Christophe Veaux, and Kirsten MacDonald, Centre for Speech Technology Research, University of Edinburgh. [CSTR VCTK Corpus v0.92](https://datashare.ed.ac.uk/handle/10283/3443), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). |
| American English | 3 bundled | Open Home Foundation [Joe](https://mozilladatacollective.com/datasets/cmid3cyuy00gnnv07mo54373n), [Mike](https://github.com/OHF-Voice/voice-datasets), and Rhasspy [Kathleen](https://github.com/rhasspy/dataset-voice-kathleen). [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/). |
| Lahgtna, Chatterbox V3, Qwen, Higgs and IndexTTS | 25 separately hosted files | See the original sources in [upstream.json](upstream.json). |

The [catalog](demo-clips.json) defines groups, labels, source pages, and published transcripts. [Supplemental transcripts](reference-transcripts.json) include machine transcriptions that should be checked before cloning. Joe and Mike transcripts are defined in [catalog.py](../../catalog.py).

Run `python3 scripts/install_references.py` to install the bundled audio and catalog. Pass `--upstream` to fetch separately hosted demo clips for local use, or `--data-dir /path/to/data` to select the destination. Existing files and catalog entries are kept. Restart the app after installing references.

The IndexTTS sad emotion example derived from IEMOCAP is listed for manual import. Consult the [USC IEMOCAP release terms](https://sail.usc.edu/iemocap/iemocap_release.htm) for the recording and transcript.
