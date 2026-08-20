"""Jetons de style steamOvap portés depuis le design system.

Source : `_ds/steamovap-design-system-*/tokens.css` du projet Claude Design.
Les rampes de couleur, l'échelle typographique, les rayons et les ombres sont
repris à l'identique ; seules les polices changent, Bricolage Grotesque n'étant
pas distribuée avec le poste — la pile de repli reste dans le même registre
grotesque.
"""

# ---------------------------------------------------------------------------
# Rampes de marque
# ---------------------------------------------------------------------------
PRIMARY = {
    50: "#d9edfc", 100: "#b3dcfa", 200: "#8ecbf8", 300: "#68b9f5",
    400: "#42a8f3", 500: "#1D97F1", 600: "#187dc8", 700: "#1364a0",
    800: "#0e4b78", 900: "#093250", 950: "#041928",
}
SECONDARY = {
    50: "#d4dbea", 100: "#aab8d5", 200: "#7f95c0", 300: "#5571ab",
    400: "#2a4e96", 500: "#002B81", 600: "#00236b", 700: "#001c58",
    800: "#001540", 900: "#000e2b", 950: "#000715",
}
LAVENDER = {
    50: "#eeeffa", 100: "#dedff5", 200: "#cddff0", 300: "#bdc0eb",
    400: "#acb0e6", 500: "#9CA1E2", 600: "#8286bc", 700: "#686b96",
    800: "#4e5071", 900: "#34354b", 950: "#1a1a25",
}
GREY = {
    50: "#eff0f1", 100: "#dfe2e3", 200: "#d0d3d6", 300: "#c0c5c8",
    400: "#b0b6ba", 500: "#A1A8AD", 600: "#868c90", 700: "#6b7073",
    800: "#505456", 900: "#353839", 950: "#1a1c1c",
}
LIME = {
    50: "#fafff4", 100: "#f6ffe9", 200: "#f2ffde", 300: "#eeffd3",
    400: "#eaffc8", 500: "#E6FFBD", 600: "#bfd49d", 700: "#99aa7e",
    800: "#737f5e", 900: "#4c553f", 950: "#262a1f",
}

# ---------------------------------------------------------------------------
# Alias sémantiques
# ---------------------------------------------------------------------------
BG = "#F6F8FB"
SURFACE = "#FFFFFF"
SURFACE_ALT = GREY[50]
BORDER = GREY[200]
BORDER_STRONG = GREY[400]
TEXT = SECONDARY[900]
TEXT_MUTED = GREY[700]
TEXT_INVERT = "#FFFFFF"
ACCENT = PRIMARY[500]

SUCCESS = LIME[800]
SUCCESS_BG = LIME[200]
WARNING = "#B4802B"
WARNING_BG = "#F6E8CE"
ERROR = "#B8341C"
ERROR_BG = "#FADFD7"
INFO = PRIMARY[500]
INFO_BG = PRIMARY[50]

# ---------------------------------------------------------------------------
# Typographie
# ---------------------------------------------------------------------------
FONT_STACK = ('"Bricolage Grotesque", "Fira Sans", "Inter", "Cantarell", '
              '"DejaVu Sans", sans-serif')
MONO_STACK = '"Fira Mono", "DejaVu Sans Mono", "Liberation Mono", monospace'

SIZE_H4, LH_H4 = 26, 32
SIZE_H6, LH_H6 = 18, 24
SIZE_BODY = 14
SIZE_SMALL = 12
SIZE_EYEBROW = 11

W_REGULAR, W_MEDIUM, W_SEMI, W_BOLD = 400, 500, 600, 700

# ---------------------------------------------------------------------------
# Espacement, rayons, ombres
# ---------------------------------------------------------------------------
S1, S2, S3, S4, S5, S6 = 4, 8, 12, 16, 24, 32
R_SM, R_MD, R_LG = 4, 8, 12


def stylesheet() -> str:
    """Feuille de style Qt globale de la console."""
    return f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: {FONT_STACK};
    font-size: {SIZE_BODY}px;
}}

QLabel {{ background: transparent; }}

QToolTip {{
    background: {SECONDARY[800]};
    color: {TEXT_INVERT};
    border: 1px solid {SECONDARY[500]};
    padding: 6px 8px;
    border-radius: {R_SM}px;
}}

/* --- Cartes ---------------------------------------------------------- */
QFrame#card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {R_MD}px;
}}
QFrame#cardAlt {{
    background: {SURFACE_ALT};
    border: 1px solid {BORDER};
    border-radius: {R_MD}px;
}}
QFrame#board {{
    background: {SECONDARY[800]};
    border: 1px solid {SECONDARY[500]};
    border-radius: {R_MD}px;
}}
QFrame#chip {{
    background: {SECONDARY[950]};
    border: 1px solid {SECONDARY[400]};
    border-radius: {R_SM}px;
}}
QFrame#banner {{
    background: {INFO_BG};
    border: 1px solid {PRIMARY[100]};
    border-radius: {R_MD}px;
}}
QFrame#separator {{ background: {BORDER}; border: none; }}

/* --- Typographie ----------------------------------------------------- */
QLabel#title {{
    font-size: {SIZE_H4}px;
    font-weight: {W_SEMI};
    color: {SECONDARY[500]};
}}
QLabel#eyebrow {{
    font-size: {SIZE_EYEBROW}px;
    font-weight: {W_SEMI};
    letter-spacing: 1.4px;
    color: {PRIMARY[500]};
}}
QLabel#sectionTitle {{
    font-size: {SIZE_H6}px;
    font-weight: {W_SEMI};
    color: {SECONDARY[500]};
}}
QLabel#muted {{ color: {TEXT_MUTED}; font-size: {SIZE_SMALL}px; }}
QLabel#pin {{
    font-family: {MONO_STACK};
    font-weight: {W_SEMI};
    color: {PRIMARY[600]};
}}
QLabel#mono {{ font-family: {MONO_STACK}; }}
QLabel#monoMuted {{
    font-family: {MONO_STACK};
    font-size: {SIZE_SMALL}px;
    color: {GREY[600]};
}}
QLabel#value {{
    font-family: {MONO_STACK};
    font-weight: {W_SEMI};
    color: {SECONDARY[500]};
}}
QLabel#clock {{
    font-family: {MONO_STACK};
    font-size: 20px;
    font-weight: {W_SEMI};
    color: {SECONDARY[500]};
}}
QLabel#boardText {{ color: {TEXT_INVERT}; background: transparent; }}
QLabel#boardMono {{
    font-family: {MONO_STACK};
    font-size: {SIZE_SMALL}px;
    color: {LAVENDER[300]};
    background: transparent;
}}

/* --- Boutons ---------------------------------------------------------- */
QPushButton {{
    background: {SURFACE};
    border: 1px solid {BORDER_STRONG};
    border-radius: {R_SM}px;
    padding: 7px 14px;
    font-weight: {W_MEDIUM};
    color: {SECONDARY[500]};
}}
QPushButton:hover {{ border-color: {PRIMARY[500]}; color: {PRIMARY[600]}; }}
QPushButton:pressed {{ background: {PRIMARY[50]}; }}
QPushButton:disabled {{ color: {GREY[500]}; border-color: {BORDER}; }}

QPushButton#primary {{
    background: {PRIMARY[500]};
    border: 1px solid {PRIMARY[500]};
    color: {TEXT_INVERT};
    font-weight: {W_SEMI};
    padding: 8px 20px;
}}
QPushButton#primary:hover {{ background: {PRIMARY[600]}; border-color: {PRIMARY[600]}; }}

QPushButton#secondary {{
    background: {SECONDARY[500]};
    border: 1px solid {SECONDARY[500]};
    color: {TEXT_INVERT};
    font-weight: {W_SEMI};
    padding: 8px 20px;
}}
QPushButton#secondary:hover {{ background: {SECONDARY[600]}; border-color: {SECONDARY[600]}; }}

QPushButton#macro {{
    font-family: {MONO_STACK};
    font-size: {SIZE_SMALL}px;
    background: {SURFACE_ALT};
    padding: 6px 10px;
    color: {SECONDARY[700]};
}}
QPushButton#macro:hover {{
    background: {PRIMARY[50]};
    border-color: {PRIMARY[500]};
    color: {PRIMARY[600]};
}}

/* --- Curseurs --------------------------------------------------------- */
QSlider::groove:horizontal {{
    height: 4px;
    background: {GREY[200]};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {PRIMARY[500]};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {SURFACE};
    border: 2px solid {PRIMARY[500]};
    width: 12px;
    height: 12px;
    margin: -6px 0;
    border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{ background: {PRIMARY[50]}; }}

/* --- Zones défilantes et listes --------------------------------------- */
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {GREY[300]};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {GREY[400]}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: transparent; width: {S5}px; }}
QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; }}
QStatusBar QLabel {{ color: {TEXT_MUTED}; font-size: {SIZE_SMALL}px; }}
"""
