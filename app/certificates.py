"""One SVG template, with escaped text and no external resources."""

from html import escape


def render_certificate(name: str, title: str, issuer: str, issued_on: str, certificate_id: str) -> str:
    def text(value: str, y: int, size: int, color: str = "#183449") -> str:
        # Keep long fields inside the border without depending on font metrics.
        fit = ' textLength="900" lengthAdjust="spacingAndGlyphs"' if len(value) * size * 0.65 > 900 else ""
        return f'<text x="600" y="{y}" text-anchor="middle" font-size="{size}" fill="{color}"{fit}>{escape(value)}</text>'

    return '\n'.join([
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="850" viewBox="0 0 1200 850">',
        '<rect width="1200" height="850" fill="#fffdf7"/>',
        '<rect x="30" y="30" width="1140" height="790" fill="none" stroke="#183449" stroke-width="4"/>',
        '<rect x="44" y="44" width="1112" height="762" fill="none" stroke="#bc9653" stroke-width="2"/>',
        '<g font-family="Georgia, serif">',
        text("CERTIFICATE OF COMPLETION", 185, 42),
        text("This certificate is presented to", 280, 24),
        text(name, 375, 52),
        '<path d="M300 410 H900" stroke="#bc9653" stroke-width="2"/>',
        text("for completing", 465, 24),
        text(title, 525, 34),
        text(issuer, 645, 26),
        text(f"Issued on {issued_on}", 692, 20),
        text(f"Certificate ID: {certificate_id}", 765, 14),
        '</g></svg>',
    ])
