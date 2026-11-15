# Long-form regression checks

Run from the repository on Linux with the web Python environment. Tests use their own temporary database and synthetic audio. They do not load a TTS model or change the live library. The document checks require the same FFmpeg, Poppler, libmobi and English/Arabic Tesseract tools as the app.

Test-only dependencies can be installed outside the web environment:

```sh
python3 -m pip install --target .runtime/test-deps httpx pypdf
npm install --prefix .runtime/test-js --no-save jsdom@24 fake-indexeddb@6
PYTHONPATH=.:.runtime/test-deps .runtime/web/bin/python -m unittest discover -s tests -p 'test_*.py' -v
PYTHONPATH=.:.runtime/test-deps .runtime/web/bin/python tests/render_studio.py .runtime/test-studio.html
STUDIO_TEST_HTML=.runtime/test-studio.html NODE_PATH=.runtime/test-js/node_modules node tests/test_studio.cjs
```

The DOM checks simulate HTML, events and IndexedDB in Node. They are not a substitute for a signed-in public browser check through Pangolin, or listening to actual model output.
