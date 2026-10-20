# Build the three submission PDFs: crop the screenshots, compile the technical document, then the security
# self-assessment and the execution evidence from their Markdown sources (build_docs.py).
all:
	uv run --with pillow python make_figures.py
	tectonic -X compile main.tex
	cp main.pdf ../Corridor_AI_Technical_Document.pdf
	uv run --with pillow python build_docs.py
