import os
from PyQt6.QtWidgets import QGraphicsDropShadowEffect
from PyQt6.QtGui import QColor
from PIL import Image


def apply_shadow(widget):
    """给容器应用统一的阴影效果，制造悬浮感"""
    shadow = QGraphicsDropShadowEffect()
    shadow.setBlurRadius(35)
    shadow.setColor(QColor(0, 0, 0, 100))
    shadow.setOffset(0, 10)
    widget.setGraphicsEffect(shadow)


def get_global_stylesheet(image_path):
    """提取图片主色调，并生成覆盖全局的透明磨砂 QSS"""
    # 默认色彩
    panel_bg = "rgba(30, 30, 30, 0.45)"
    text_color = "#ffffff"
    btn_bg = "rgba(100, 181, 246, 0.85)"
    btn_hover = "rgba(66, 165, 245, 0.95)"
    btn_text = "#ffffff"
    input_bg = "rgba(0, 0, 0, 0.35)"
    border_color = "rgba(255, 255, 255, 0.15)"
    nav_text = "#ffffff"
    nav_hover = "rgba(255, 255, 255, 0.2)"
    nav_sel_bg = "rgba(255, 255, 255, 0.85)"
    nav_sel_text = "#000000"

    if os.path.exists(image_path):
        try:
            img = Image.open(image_path).convert("RGB")
            img.thumbnail((100, 100))
            colors = img.getcolors(10000)
            colors.sort(key=lambda x: x[0], reverse=True)

            dom_r, dom_g, dom_b = colors[0][1]
            for count, (r, g, b) in colors:
                saturation = max(r, g, b) - min(r, g, b)
                if saturation > 30 and 40 < r < 240 and 40 < g < 240 and 40 < b < 240:
                    dom_r, dom_g, dom_b = r, g, b
                    break

            luminance = 0.299 * dom_r + 0.587 * dom_g + 0.114 * dom_b

            # 动态明暗主题自适应
            if luminance > 135:
                panel_bg = "rgba(255, 255, 255, 0.45)"
                text_color = "#1c2833"
                input_bg = "rgba(255, 255, 255, 0.65)"
                border_color = "rgba(255, 255, 255, 0.5)"
                nav_text = "#2c3e50"
                nav_hover = "rgba(255, 255, 255, 0.5)"
                nav_sel_bg = "rgba(255, 255, 255, 0.95)"
                nav_sel_text = "#d84315"
            else:
                panel_bg = "rgba(20, 20, 20, 0.55)"
                text_color = "#fdfefe"
                input_bg = "rgba(0, 0, 0, 0.45)"
                border_color = "rgba(255, 255, 255, 0.15)"
                nav_text = "#fdfefe"
                nav_hover = "rgba(0, 0, 0, 0.4)"
                nav_sel_bg = "rgba(30, 30, 30, 0.85)"
                nav_sel_text = "#64b5f6"

            btn_r = min(255, dom_r + 20)
            btn_g = min(255, dom_g + 20)
            btn_b = min(255, dom_b + 20)
            btn_bg = f"rgba({btn_r}, {btn_g}, {btn_b}, 0.85)"
            btn_hover = f"rgba({min(255, btn_r + 30)}, {min(255, btn_g + 30)}, {min(255, btn_b + 30)}, 0.95)"
            btn_lum = 0.299 * btn_r + 0.587 * btn_g + 0.114 * btn_b
            btn_text = "#ffffff" if btn_lum < 150 else "#1c2833"
        except Exception:
            pass

    # 返回全局样式
    return f"""
        QWidget#mainWrapper {{
            background-color: transparent; 
        }}
        QTabWidget::pane {{ border: none; background: transparent; }}
        QTabBar {{ alignment: center; }}
        QTabBar::tab {{
            background: transparent; 
            color: {nav_text}; 
            padding: 10px 25px;
            margin: 5px 6px 15px 6px; 
            border-radius: 12px; 
            font-weight: bold;
            font-size: 15px;
        }}
        QTabBar::tab:selected {{
            background-color: {nav_sel_bg}; 
            color: {nav_sel_text}; 
            border: 1px solid {border_color}; 
        }}
        QTabBar::tab:hover:!selected {{ background-color: {nav_hover}; }}

        QFrame#container {{
            background-color: {panel_bg};
            border-radius: 12px;
            border: 1px solid {border_color};
        }}
        QLabel, QCheckBox, QRadioButton {{ color: {text_color}; font-weight: bold; font-size: 13px; background: transparent; }}
        QLineEdit, QTextEdit, QListWidget {{
            background-color: {input_bg};
            border: 1px solid {border_color};
            border-radius: 8px;
            padding: 6px;
            color: {text_color};
            selection-background-color: {btn_bg};
        }}
        QPushButton {{
            background-color: {btn_bg};
            color: {btn_text};
            border-radius: 8px;
            padding: 8px 15px;
            font-weight: bold;
            font-size: 13px;
            border: none;
        }}
        QPushButton:hover {{ background-color: {btn_hover}; }}
        QPushButton:disabled {{ background-color: rgba(150, 150, 150, 0.4); color: rgba(255, 255, 255, 0.5); }}

        QScrollBar:vertical {{ border: none; background: transparent; width: 8px; margin: 0px; }}
        QScrollBar::handle:vertical {{ background: rgba(150, 150, 150, 0.5); border-radius: 4px; }}
        QScrollBar::handle:vertical:hover {{ background: rgba(150, 150, 150, 0.8); }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}

        /* 标题栏专用完全透明无边框样式 */
        QPushButton#titleBtn {{
            background: transparent;
            color: {nav_text};
            font-size: 16px;
            border-radius: 0px;
        }}
        QPushButton#titleBtn:hover {{ background-color: {nav_hover}; }}
        QPushButton#closeBtn {{ background: transparent; color: {nav_text}; font-size: 16px; border-radius: 0px; }}
        QPushButton#closeBtn:hover {{ background-color: #ff5252; color: white; border-top-right-radius: 15px; }}

        QPushButton#skinBtn {{
            background-color: transparent;
            color: {nav_text};
            border: 1px solid {border_color};
            border-radius: 12px;
            padding: 5px 15px;
            font-size: 13px;
            margin-right: 15px;
        }}
        QPushButton#skinBtn:hover {{ background-color: {nav_hover}; }}
    """