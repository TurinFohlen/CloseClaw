"""
upload_locator.py — 用 opencv 模板匹配定位上传按钮

只在首次运行或配置刷新时调用，结果写进 config（坐标写死）。
热路径直接用缓存坐标，不重复跑 opencv。

工作流：
    1. 截全屏（mss）
    2. 模板匹配找上传图标
    3. 返回按钮中心坐标 (x, y)
    4. 写入 /shared/upload_btn.json 缓存

重新定位（界面更新后）：
    python -m closeclaw.control.upload_locator --recalibrate
"""

import json
import subprocess
import sys
from pathlib import Path

CACHE_FILE = Path("/shared/upload_btn.json")
TEMPLATE_DIR = Path(__file__).parent / "templates"

# 模板图片路径（light/dark 两套）
TEMPLATES = {
    "light": TEMPLATE_DIR / "upload_btn_light.png",
    "dark":  TEMPLATE_DIR / "upload_btn_dark.png",
}

# opencv 置信度阈值
THRESHOLD = 0.80


def _find_button_opencv(screenshot_path: Path) -> tuple[int, int] | None:
    """
    在截图里模板匹配上传按钮，返回中心坐标。
    需要 opencv-python（可选依赖）。
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        raise ImportError(
            "opencv-python not installed. "
            "pip install opencv-python  or  pip install closeclaw[cv]"
        )

    screen = cv2.imread(str(screenshot_path))
    if screen is None:
        raise FileNotFoundError(f"Screenshot not found: {screenshot_path}")

    best_val = 0.0
    best_loc = None
    best_w = best_h = 0

    for theme, tpl_path in TEMPLATES.items():
        if not tpl_path.exists():
            continue
        template = cv2.imread(str(tpl_path))
        if template is None:
            continue

        h, w = template.shape[:2]
        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val > best_val:
            best_val = max_val
            best_loc = max_loc
            best_w, best_h = w, h

    if best_val < THRESHOLD or best_loc is None:
        return None

    cx = best_loc[0] + best_w // 2
    cy = best_loc[1] + best_h // 2
    return cx, cy


def _take_screenshot(out_path: Path) -> None:
    try:
        import mss
        with mss.mss() as sct:
            sct.shot(output=str(out_path))
    except ImportError:
        # fallback: scrot
        subprocess.run(["scrot", str(out_path)], check=True)


def locate_upload_button(recalibrate: bool = False) -> tuple[int, int]:
    """
    返回上传按钮的屏幕坐标 (x, y)。
    优先读缓存，recalibrate=True 强制重新定位。
    """
    if not recalibrate and CACHE_FILE.exists():
        data = json.loads(CACHE_FILE.read_text())
        return data["x"], data["y"]

    # 截图
    tmp_shot = Path("/tmp/closeclaw_locate.png")
    _take_screenshot(tmp_shot)

    coords = _find_button_opencv(tmp_shot)
    if coords is None:
        raise RuntimeError(
            "Upload button not found in screenshot. "
            "Make sure claude.ai is visible and the upload icon is on screen. "
            f"Check templates in {TEMPLATE_DIR}."
        )

    x, y = coords
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps({"x": x, "y": y}))
    print(f"[upload_locator] Upload button at ({x}, {y}) → cached to {CACHE_FILE}")
    return x, y


if __name__ == "__main__":
    recal = "--recalibrate" in sys.argv
    x, y = locate_upload_button(recalibrate=recal)
    print(f"Upload button: ({x}, {y})")
