"""GUI executor model client — the swappable `infer()` seam.

Architecture §5/§6 + Roadmap §A.6 item 3: `model_client.infer()` is the
single-method interface every GUI executor backend implements (UI-TARS now,
Qwen-UI-Agent grounding mode in Phase 2). Nothing downstream of this file
knows which model produced the action — UI-TARS output is parsed and
normalized to `shared.action_schema.Action` here and only here.

Per Architecture §6, the action schema is normalized to the Gateway's own
format immediately on return from the model. UI-TARS coordinates are
absolute pixels in the *resized* image space (the Qwen2.5-VL processor's
`smart_resize` output); this module maps them to screen points using the
display size carried on the `Screenshot`.

ED #11 / Master Prompt §5 second-planner smoke test: this is the ONLY place
an LLM call happens in the GUI dispatch path.
"""

from __future__ import annotations

import ast
import logging
import math
import os
import re
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from shared.action_schema import Action, ActionType, Screenshot

logger = logging.getLogger("cua_gateway.model_client")

DEFAULT_MODEL_ID = "mlx-community/UI-TARS-1.5-7B-4bit"


def resolve_model_id(explicit: str | None) -> str:
    """Resolve the GUI-agent model: explicit arg, then env override
    (CUA_GATEWAY_MODEL = HF id or local dir, CUA_MODEL_DIR = local dir),
    then the default HF checkpoint.

    `CUA_MODEL_DIR` lets the distributed app point the gateway at a bundled
    or pre-provisioned local copy of the model (no HF download at runtime).
    """
    if explicit:
        return explicit
    for env in ("CUA_GATEWAY_MODEL", "CUA_MODEL_DIR"):
        value = os.environ.get(env)
        if value:
            return value
    return DEFAULT_MODEL_ID

# ---------------------------------------------------------------------------
# Model-agnostic contract
# ---------------------------------------------------------------------------


class ModelClientError(Exception):
    pass


class UnsupportedModelAction(ModelClientError):
    """The model produced an action (e.g. `wait`, `finished`) the Gateway
    does not dispatch in MVP. Caller decides how to handle it."""

    def __init__(self, name: str, args: dict[str, Any] | None = None):
        self.name = name
        self.args = args or {}
        super().__init__(f"model action not dispatchable in MVP: {name}({self.args})")


class ModelClient(ABC):
    @abstractmethod
    def infer(self, screenshot: Screenshot, instruction: str, history: list[str] | None = None) -> Action:
        """Ground one action from a screenshot + instruction.

        Returns a normalized `Action` in *screen point* coordinates, ready
        for the OS adapter's `dispatch()`. Raises `ModelClientError` on
        malformed output, `UnsupportedModelAction` for non-dispatchable
        actions the model chose.
        """


# ---------------------------------------------------------------------------
# UI-TARS prompt format (OSWorld-style, verified against the checkpoint)
# ---------------------------------------------------------------------------

UI_TARS_ACTION_SPACE = """click(start_box='<|box_start|>(x1,y1)<|box_end|>')
left_double(start_box='<|box_start|>(x1,y1)<|box_end|>')
right_single(start_box='<|box_start|>(x1,y1)<|box_end|>')
drag(start_box='<|box_start|>(x1,y1)<|box_end|>', end_box='<|box_start|>(x3,y3)<|box_end|>')
hotkey(key='')
type(content='') #If you want to submit your input, use "\\n" at the end of `content`.
scroll(start_box='<|box_start|>(x1,y1)<|box_end|>', direction='down or up or right or left')
wait() #Sleep for 5s and take a screenshot to check for any changes.
finished()
"""

UI_TARS_SYSTEM_TEMPLATE = """You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task.

## Output Format
```
Thought: ...
Action: ...
```

## Action Space
{action_space}

## Note
- Use English in `Thought` part.
- Write a small plan and finally summarize your next action (with its target element) in one sentence in `Thought` part.

## User Instruction
The task is: {instruction}
Please give me the grounding action according to the last screenshot."""

# qwen2.5-vl smart_resize parameters (mlx-vlm Qwen3VLImageProcessor
# instance for this checkpoint — verified at runtime):
IMAGE_FACTOR = 28
MIN_PIXELS = 3136
MAX_PIXELS = 12845056


def smart_resize(height: int, width: int) -> tuple[int, int]:
    """Replicates mlx-vlm's `_smart_resize_image` for qwen2.5-vl exactly."""
    h_bar = round(height / IMAGE_FACTOR) * IMAGE_FACTOR
    w_bar = round(width / IMAGE_FACTOR) * IMAGE_FACTOR
    if h_bar * w_bar > MAX_PIXELS:
        beta = (height * width / MAX_PIXELS) ** 0.5
        h_bar = max(IMAGE_FACTOR, math.floor(height / beta / IMAGE_FACTOR) * IMAGE_FACTOR)
        w_bar = max(IMAGE_FACTOR, math.floor(width / beta / IMAGE_FACTOR) * IMAGE_FACTOR)
    elif h_bar * w_bar < MIN_PIXELS:
        beta = (MIN_PIXELS / (height * width)) ** 0.5
        h_bar = max(IMAGE_FACTOR, math.ceil(height * beta / IMAGE_FACTOR) * IMAGE_FACTOR)
        w_bar = max(IMAGE_FACTOR, math.ceil(width * beta / IMAGE_FACTOR) * IMAGE_FACTOR)
    return h_bar, w_bar


# ---------------------------------------------------------------------------
# UI-TARS output parsing + normalization
# ---------------------------------------------------------------------------

BOX_RE = re.compile(r"<\|box_start\|>\((\d+)\s*,\s*(\d+)\)<\|box_end\|>")
POINT_RE = re.compile(r"\(\s*(\d+)\s*,\s*(\d+)\s*\)")

HOTKEY_ALIASES = {
    "arrowleft": "left",
    "arrowright": "right",
    "arrowup": "up",
    "arrowdown": "down",
    "space": "space",
    "esc": "escape",
}


def parse_model_output(text: str) -> dict[str, Any]:
    """Extract the Action call from a UI-TARS response.

    Accepts optional `Thought: ...` prefix and `click(...)` / `click(start_box='...')`
    / `<|box_start|>`-tagged variants (1.5-era and 2.x-era formats).
    Returns {"name": ..., "args": {...}} or raises ModelClientError.
    """
    text = text.strip()
    if "Action: " in text:
        text = text.split("Action: ")[-1].strip()
    action_str = text.split("\n")[0].strip()
    if not action_str.endswith(")"):
        # find the last call ending
        idx = action_str.rfind(")")
        if idx == -1:
            raise ModelClientError(f"no action call found in model output: {text!r}")
        action_str = action_str[: idx + 1]
    node = ast.parse(action_str, mode="eval")
    call = node.body
    if not isinstance(call, ast.Call):
        raise ModelClientError(f"model output is not a call: {action_str!r}")
    name = call.func.id if isinstance(call.func, ast.Name) else None
    if name is None:
        raise ModelClientError(f"cannot resolve action name: {action_str!r}")
    args: dict[str, Any] = {}
    for kw in call.keywords:
        if isinstance(kw.value, ast.Constant):
            args[kw.arg] = kw.value.value
        elif isinstance(kw.value, ast.Str):
            args[kw.arg] = kw.value.s
    return {"name": name, "args": args}


def parse_box(value: Any) -> tuple[int, int] | None:
    """Extract (x, y) from `<|box_start|>(x,y)<|box_end|>` or `(x,y)` or `[x,y]`."""
    if not isinstance(value, str):
        return None
    m = BOX_RE.search(value)
    if not m:
        m = POINT_RE.search(value)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


class UiTarsActionNormalizer:
    """UI-TARS call -> shared.action_schema.Action in screen-point coords."""

    def __init__(self, image_width: int, image_height: int, display_width: int, display_height: int):
        self.image_width = image_width
        self.image_height = image_height
        self.display_width = display_width
        self.display_height = display_height
        self.resized_h, self.resized_w = smart_resize(image_height, image_width)

    def to_screen(self, model_x: int, model_y: int) -> tuple[int, int]:
        """Model (resized) coords -> image pixels -> screen points."""
        x_px = model_x / self.resized_w * self.image_width
        y_px = model_y / self.resized_h * self.image_height
        sx = x_px / self.image_width * self.display_width
        sy = y_px / self.image_height * self.display_height
        return int(round(sx)), int(round(sy))

    def normalize(self, name: str, args: dict[str, Any], target_hint: str) -> Action:
        params: dict[str, Any]
        if name == "click":
            box = parse_box(args.get("start_box"))
            if box is None:
                raise ModelClientError(f"click without start_box: {args}")
            params = {"point": list(self.to_screen(*box))}
            return Action(type=ActionType.CLICK, target_hint=target_hint, params=params)
        if name == "left_double":
            box = parse_box(args.get("start_box"))
            if box is None:
                raise ModelClientError(f"left_double without start_box: {args}")
            params = {"point": list(self.to_screen(*box))}
            return Action(type=ActionType.DOUBLE_CLICK, target_hint=target_hint, params=params)
        if name == "right_single":
            box = parse_box(args.get("start_box"))
            if box is None:
                raise ModelClientError(f"right_single without start_box: {args}")
            params = {"point": list(self.to_screen(*box))}
            return Action(type=ActionType.RIGHT_CLICK, target_hint=target_hint, params=params)
        if name == "drag":
            start = parse_box(args.get("start_box"))
            end = parse_box(args.get("end_box"))
            if start is None or end is None:
                raise ModelClientError(f"drag without start/end box: {args}")
            params = {
                "from": list(self.to_screen(*start)),
                "to": list(self.to_screen(*end)),
            }
            return Action(type=ActionType.DRAG, target_hint=target_hint, params=params)
        if name == "type":
            return Action(
                type=ActionType.TYPE,
                target_hint=target_hint,
                params={"text": args.get("content", "")},
            )
        if name == "hotkey":
            combo = _normalize_hotkey(args.get("key", ""))
            return Action(
                type=ActionType.KEY,
                target_hint=target_hint,
                params={"key": combo},
            )
        if name == "scroll":
            box = parse_box(args.get("start_box"))
            direction = args.get("direction", "down")
            deltas = {
                "down": (0, 3),
                "up": (0, -3),
                "left": (-3, 0),
                "right": (3, 0),
            }
            params: dict[str, Any] = {"delta": deltas.get(direction, (0, 3))}
            if box is not None:
                params["point"] = list(self.to_screen(*box))
            return Action(type=ActionType.SCROLL, target_hint=target_hint, params=params)
        if name in ("wait", "finished"):
            raise UnsupportedModelAction(name, args)
        raise ModelClientError(f"unknown action name: {name!r}")


def _normalize_hotkey(key: str) -> str:
    """'cmd c' / 'ctrl c' / 'arrowleft' -> '+'-joined combo for os_adapter."""
    parts = [HOTKEY_ALIASES.get(p.lower(), p.lower()) for p in key.split()]
    return "+".join(parts)


# ---------------------------------------------------------------------------
# MLX-backed UI-TARS client
# ---------------------------------------------------------------------------


class UiTarsMLXClient(ModelClient):
    """UI-TARS-1.5-7B-4bit served locally via mlx-vlm (Architecture §14).

    Model loading is lazy (first `infer` call) so unit tests and the rest
    of the Gateway never pay for it. The mlx import is deferred for the
    same reason.
    """

    def __init__(self, model_id: str | None = None):
        self.model_id = resolve_model_id(model_id)
        # A local directory path needs no HF download and no offline pinning.
        self._local_dir = bool(self.model_id) and Path(self.model_id).is_dir()
        self._model = None
        self._processor = None
        self._config = None
        self._lock = threading.RLock()
        self.warmup_started = False
        # Inference cache: (screenshot_hash, instruction) -> Action
        self._infer_cache = {}
        self._cache_max_size = 100

    def warmup(self) -> None:
        """Load the model in the background (idempotent, daemon thread).

        The first `infer` call otherwise pays the full load cost (10+ min
        cold, ~16s cached) inside the MCP request window. Called once at
        gateway startup so the model is ready by the time the planner
        issues its first `gui.act`.
        """
        with self._lock:
            if self.warmup_started or self._model is not None:
                return
            self.warmup_started = True

        def _load_and_report() -> None:
            try:
                self._ensure_loaded()
                logger.info("UI-TARS warmup complete: model loaded")
            except Exception:
                logger.exception("UI-TARS warmup failed; first infer will retry load")

        threading.Thread(target=_load_and_report, name="uitars-warmup", daemon=True).start()

    def _ensure_loaded(self):
        with self._lock:
            if self._model is None:
                if not self._local_dir:
                    self._force_offline_if_cached()
                import mlx.core as mx
                mx.set_default_device(mx.Device(mx.gpu))
                from mlx_vlm import load
                from mlx_vlm.prompt_utils import apply_chat_template  # noqa: F401
                from mlx_vlm.utils import load_config

                self._model, self._processor = load(self.model_id)
                self._config = load_config(self.model_id)
                logger.info(f"UI-TARS loaded on device: {mx.default_device()}")

    def _force_offline_if_cached(self) -> None:
        """Use the HF cache when the model is already downloaded.

        mlx-vlm's loader can hang for minutes on network probes of the HF
        hub even when every file is already cached locally. When the model
        repo is present under the HF cache, pin the hub to offline mode so
        the first `gui.act` loads in seconds instead of hanging.
        """
        repo_dir = "models--" + UiTarsMLXClient.model_id_for_cache(self.model_id)
        hf_home = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
        if (Path(hf_home) / "hub" / repo_dir).is_dir():
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        elif os.environ.get("HF_HUB_OFFLINE") != "1":
            logger.warning(
                "UI-TARS model not found in HF cache (%s); first load will download it",
                Path(hf_home) / "hub" / repo_dir,
            )

    @staticmethod
    def model_id_for_cache(model_id: str) -> str:
        return model_id.replace("/", "--")

    def infer(self, screenshot: Screenshot, instruction: str, history: list[str] | None = None) -> Action:
        with self._lock:
            return self._infer_locked(screenshot, instruction, history)

    def _infer_locked(self, screenshot: Screenshot, instruction: str, history: list[str] | None = None) -> Action:
        self._ensure_loaded()

        # Inference cache key: screenshot hash + instruction (history excluded for cache hits)
        cache_key = f"{screenshot.sha}|{instruction}"
        if cache_key in self._infer_cache:
            logger.debug(f"Inference cache hit for {cache_key[:32]}...")
            return self._infer_cache[cache_key]

        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template
        from PIL import Image
        import io

        if not screenshot.display_width or not screenshot.display_height:
            raise ModelClientError("screenshot lacks display dims; cannot map model coords")
        if history:
            hist_note = "\\n\\n".join(f"- {h}" for h in history[-3:])
            task = f"{instruction}\\n\\nRecent history:\\n{hist_note}"
        else:
            task = instruction
        prompt = UI_TARS_SYSTEM_TEMPLATE.format(
            action_space=UI_TARS_ACTION_SPACE, instruction=task
        )

        # Downscale image for faster inference (max 1280px on longest side = 720p)
        img_data = open(screenshot.ref, "rb").read()
        img = Image.open(io.BytesIO(img_data))
        max_dim = 1280
        if max(img.width, img.height) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": img},
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        formatted = apply_chat_template(self._processor, self._config, messages, num_images=1)
        from mlx_vlm import generate
        output = generate(
            self._model,
            self._processor,
            formatted,
            [img],
            max_tokens=256,
            temperature=0.0,
        )
        parsed = parse_model_output(output.text)
        normalizer = UiTarsActionNormalizer(
            screenshot.width,
            screenshot.height,
            screenshot.display_width,
            screenshot.display_height,
        )
        action = normalizer.normalize(parsed["name"], parsed["args"], target_hint=instruction)

        # Cache the result (LRU eviction)
        if len(self._infer_cache) >= self._cache_max_size:
            oldest_key = next(iter(self._infer_cache))
            del self._infer_cache[oldest_key]
        self._infer_cache[cache_key] = action

        return action


# ModelClient instance used by the Gateway executor. Lazy-loads on first use.
def get_client(model_id: str | None = None) -> ModelClient:
    global _CLIENT
    resolved = resolve_model_id(model_id)
    if _CLIENT is None or _CLIENT.model_id != resolved:
        _CLIENT = UiTarsMLXClient(resolved)
    return _CLIENT


_CLIENT: UiTarsMLXClient | None = None