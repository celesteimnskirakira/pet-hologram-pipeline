"""Prompt construction for the two generation steps.

Identity preservation is the whole game here. The still prompt is built from a
structured trait description extracted from the upload, so the image model gets
explicit constraints instead of a vague "keep it similar".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

TRAIT_SCHEMA = {
    "species": "cat | dog | other",
    "breed_guess": "short breed or mix guess",
    "body_build": "size and build, e.g. slender, stocky, long-bodied",
    "coat_length": "short | medium | long | curly | hairless",
    "coat_pattern": "solid | tabby | bicolor | tricolor | tuxedo | merle | brindle | pointed | spotted",
    "primary_colors": "ordered list of dominant coat colors",
    "markings": "distinctive markings with locations, e.g. white blaze on muzzle, white left front paw",
    "face_shape": "muzzle length, cheek fullness, head shape",
    "eye_color": "eye color",
    "eye_shape": "eye shape and set",
    "ear_shape": "ear shape and carriage, e.g. upright triangular, folded, floppy",
    "nose_color": "nose leather color",
    "whisker_notes": "whisker/eyebrow notes if visible",
    "tail_notes": "tail length, plume, curl, tip color if visible",
    "accessories": "collar/tag/bandana details, or none",
    "asymmetries": "any left/right differences that must be preserved",
}

VISION_SYSTEM = (
    "You are a precise animal-identity annotator. Describe only what is visible. "
    "Never invent traits. If a trait is not visible, use \"not visible\". "
    "Be specific about colors, markings, and which side of the body they sit on."
)

VISION_INSTRUCTION = (
    "Analyse this pet photo and return a single JSON object with exactly these keys:\n"
    + json.dumps(TRAIT_SCHEMA, ensure_ascii=False, indent=2)
    + "\n\nRules:\n"
    "- Output raw JSON only, no markdown fence, no commentary.\n"
    "- Use short English phrases as values; primary_colors and markings are arrays.\n"
    "- Note the pet's left/right from the viewer's perspective and say so explicitly.\n"
    "- If the animal is neither cat nor dog, still fill species with the best guess."
)

POSE_TEXT = {
    "curled_side": (
        "lying down curled on its side, head resting on its own front paws, "
        "body coiled into a soft comma shape, eyes closed, fully asleep"
    ),
    "loaf": (
        "lying in a compact loaf position, chest on the ground, front paws tucked under, "
        "chin lowered onto the paws, eyes closed, dozing"
    ),
    "sprawl": (
        "lying belly-down and relaxed, front legs stretched forward, hind legs splayed behind, "
        "chin flat on the ground, eyes closed, deeply asleep"
    ),
    "curled_tight": (
        "curled into a tight ball, nose tucked toward the base of the tail, "
        "tail wrapped around the body, eyes closed, sound asleep"
    ),
    "side_stretch": (
        "lying stretched out on one side, legs extended loosely, belly slightly exposed, "
        "head tipped back against the ground, eyes closed, deeply relaxed"
    ),
    "scratch_neck": (
        "sitting naturally, head turned slightly, ready to lift one hind leg and scratch its neck"
    ),
    "sleep": (
        "lying down naturally curled or resting on its belly, eyes closed and peacefully asleep"
    ),
    "groom": (
        "sitting or crouching naturally, ready to lift one front paw and groom its fur"
    ),
    "walk": (
        "standing naturally and facing the camera, ready for an in-place walking cycle"
    ),
}

ACTION_LABELS = {
    "scratch_neck": "挠脖子",
    "sleep": "睡觉",
    "groom": "舔毛",
    "walk": "走路",
}
LOCAL_BOOTH_ACTIONS = tuple(ACTION_LABELS)
ACTION_PROMPT_DIR = Path(__file__).with_name("action_prompts")
ACTION_PROMPT_FILES = {
    "scratch_neck": "scratch_neck.txt",
    "sleep": "sleep.txt",
    "groom": "groom.txt",
    "walk": "walk.txt",
}

# Default action set for the roadshow display: three visually distinct sleeping
# silhouettes, so cycling between them reads as variety rather than a glitch.
ROADSHOW_POSES = ("curled_side", "loaf", "side_stretch")


@dataclass
class PetTraits:
    raw: dict = field(default_factory=dict)

    @property
    def species(self) -> str:
        value = str(self.raw.get("species", "")).strip().lower()
        if "cat" in value:
            return "cat"
        if "dog" in value or "puppy" in value:
            return "dog"
        return "pet"

    def descriptor_lines(self) -> list[str]:
        lines = []
        for key in TRAIT_SCHEMA:
            value = self.raw.get(key)
            if value in (None, "", [], "not visible", "none"):
                continue
            if isinstance(value, (list, tuple)):
                value = ", ".join(str(item) for item in value if str(item).strip())
            if not str(value).strip():
                continue
            label = key.replace("_", " ")
            lines.append(f"- {label}: {value}")
        return lines

    def identity_block(self) -> str:
        lines = self.descriptor_lines()
        if not lines:
            return "- preserve every visible feature of the reference photo exactly"
        return "\n".join(lines)

    @classmethod
    def from_text(cls, text: str) -> "PetTraits":
        payload = _loose_json(text)
        return cls(raw=payload if isinstance(payload, dict) else {})


def _loose_json(text: str) -> dict:
    """Parse JSON that may arrive wrapped in prose or a markdown fence."""
    text = (text or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = text.strip("`")
        newline = text.find("\n")
        if newline != -1:
            text = text[newline + 1 :]
        text = text.rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return {}


def still_prompt(traits: PetTraits, has_reference: bool = True) -> str:
    """Step 2 prompt: pure black background front view, identity locked."""
    subject = traits.species if traits.species != "pet" else "pet"
    reference_clause = (
        "Redraw the exact same individual animal from the reference image. "
        "This is the same pet, not a lookalike. "
        if has_reference
        else ""
    )
    return f"""{reference_clause}Produce a clean front-facing studio portrait of this {subject}.

MUST MATCH THE REFERENCE EXACTLY:
{traits.identity_block()}

COMPOSITION
- Straight-on front view, the animal facing the camera directly, head level, symmetric framing.
- Full head and chest visible, centered, with even margins; no cropping of ears or muzzle.
- Eyes open, calm neutral expression, looking into the lens.

BACKGROUND
- Pure solid black background, RGB (0, 0, 0), completely uniform.
- No gradient, no vignette, no studio backdrop texture, no floor line, no shadow cast on the background.
- No rim light spill onto the background, no glow, no haze, no props, no text, no watermark.

LIGHTING AND RENDERING
- Soft frontal key light with gentle fill so fur detail and markings stay readable.
- Photorealistic fur texture, natural coat colors, sharp focus on the eyes.
- Keep every marking in the same position and on the same side of the body as the reference.

FORBIDDEN
- Do not change coat color, pattern, markings, eye color, ear shape, or accessories.
- Do not add a collar, harness, or tag that is not in the reference.
- Do not stylise into cartoon, anime, painting, or 3D render."""


def sleep_still_prompt(traits: PetTraits, pose: str = "curled_side") -> str:
    """Bridge frame: the same pet, already asleep, on pure black.

    Asking a video model to turn a sitting, eyes-open portrait into a curled-up
    sleeping animal is a large pose change, and first/last frame conditioning
    fights it: the frames say "stay put" while the text says "lie down". So the
    pose change happens here, in image space, and the video step is left with
    nothing to do but breathe.
    """
    subject = traits.species if traits.species != "pet" else "pet"
    pose_text = POSE_TEXT.get(pose, POSE_TEXT["curled_side"])
    return f"""Redraw the exact same individual {subject} from the reference image, now {pose_text}.

This is the same pet, not a lookalike. Only the body pose changes; the animal's identity does not.

MUST MATCH THE REFERENCE EXACTLY:
{traits.identity_block()}

POSE AND COMPOSITION
- The animal is lying down and asleep, {pose_text}.
- Eyes fully closed, relaxed sleeping expression, body settled and heavy.
- Side-on three-quarter view of the resting body so the curled silhouette reads clearly.
- Whole animal inside the frame, centered, with even margins; nothing cropped.
- Resting directly on an unseen surface. No visible bed, blanket, cushion, floor, or prop.

BACKGROUND
- Pure solid black background, RGB (0, 0, 0), completely uniform.
- No gradient, no vignette, no backdrop texture, no floor line, no cast shadow.
- No rim light spill onto the background, no glow, no haze, no text, no watermark.

LIGHTING AND RENDERING
- Soft frontal key light with gentle fill so fur detail and markings stay readable.
- Photorealistic fur texture, natural coat colors, sharp focus.
- Keep every marking in the same position and on the same side of the body as the reference.

FORBIDDEN
- Do not change coat color, pattern, markings, eye color, ear shape, or accessories.
- Do not keep the animal sitting or standing. It must be lying down.
- Do not leave the eyes open.
- Do not stylise into cartoon, anime, painting, or 3D render."""


def action_video_prompt(action: str) -> str:
    """Return the exact user-supplied video prompt for one local-booth action."""
    filename = ACTION_PROMPT_FILES.get(action)
    if filename is None:
        raise ValueError(f"Unknown action prompt: {action}")
    return (ACTION_PROMPT_DIR / filename).read_text(encoding="utf-8").strip()


def action_still_prompt(traits: PetTraits, pose: str) -> str:
    """Create a bridge frame whose body pose matches the selected action."""
    if pose not in ACTION_PROMPT_FILES:
        return sleep_still_prompt(traits, pose=pose)

    subject = traits.species if traits.species != "pet" else "pet"
    start_pose = {
        "scratch_neck": (
            "sitting naturally with all four limbs anatomically correct, head turned slightly, "
            "hind paws still on the ground, ready to scratch the neck"
        ),
        "sleep": (
            "lying down naturally curled or resting on its belly, eyes fully closed, peacefully asleep"
        ),
        "groom": (
            "sitting or lightly crouching naturally, both front paws initially resting in anatomically "
            "correct positions, ready to lift one paw for grooming"
        ),
        "walk": (
            "standing squarely and facing the camera in a neutral balanced stance, all four feet visible, "
            "ready to begin an in-place walking cycle"
        ),
    }[pose]
    eye_rule = "Eyes fully closed." if pose == "sleep" else "Eyes natural and calm."
    return f"""Redraw the exact same individual {subject} from the reference image, now {start_pose}.

This is the same pet, not a lookalike. Only the body pose changes; the animal's identity does not.

MUST MATCH THE REFERENCE EXACTLY:
{traits.identity_block()}

POSE AND COMPOSITION
- The animal is {start_pose}.
- {eye_rule}
- Full body visible and centered with generous even margins; no ears, paws, legs, or tail cropped.
- Fixed straight-on camera view suitable for a square hologram animation.
- No visible bed, blanket, furniture, floor, or prop.

BACKGROUND
- Pure solid black background, RGB (0, 0, 0), completely uniform.
- No gradient, vignette, texture, floor line, cast shadow, glow, haze, text, or watermark.

IDENTITY AND ANATOMY
- Preserve coat color, pattern, markings, face, eyes, ears, body proportions, tail, and accessories.
- Keep every marking on the same side and in the same position as the reference.
- Exactly four anatomically correct limbs; no extra, missing, fused, or deformed paws.
- Photorealistic fur and constant soft frontal studio lighting.
- Do not stylise into cartoon, anime, painting, or 3D render."""


def video_prompt(traits: PetTraits, pose: str = "curled_side", loop_hint: bool = True) -> str:
    """Step 3 prompt: sleeping loop, minimal motion so head/tail frames match."""
    if pose in ACTION_PROMPT_FILES:
        supplied = action_video_prompt(pose)
        loop_lock = (
            "\n\n补充身份锁定（必须遵守）：\n"
            f"{traits.identity_block()}\n\n"
            "补充循环要求：第一帧和最后一帧必须回到相同的姿态、位置、主体大小、镜头构图和动作周期相位，确保循环播放没有明显跳变。"
            if loop_hint
            else f"\n\n补充身份锁定（必须遵守）：\n{traits.identity_block()}"
        )
        return supplied + loop_lock

    subject = traits.species if traits.species != "pet" else "pet"
    pose_text = POSE_TEXT.get(pose, POSE_TEXT["curled_side"])
    loop_text = (
        "The final frame must return to exactly the same pose, position, and framing as the first frame "
        "so the clip loops seamlessly with no visible jump. "
        if loop_hint
        else ""
    )
    return f"""The same {subject} from the reference image is {pose_text}, resting on a soft surface in a pure black void.

IDENTITY LOCK
{traits.identity_block()}

MOTION
- One single continuous shot, no cuts, no scene change.
- Only slow rhythmic breathing: the ribcage and flank rise and fall gently, about two full breaths across the clip.
- Micro motion only: a faint ear twitch, a small whisker flutter, the faintest tail tip settle.
- Eyes stay closed the entire time. The animal never lifts its head, never stands, never walks.
- No blinking open, no yawning, no looking at camera, no repositioning of the paws.

CAMERA
- Locked-off static camera on a tripod. No pan, no tilt, no zoom, no dolly, no parallax, no handheld shake.
- Framing identical from first frame to last frame.

BACKGROUND AND LIGHT
- Pure black background, unlit and empty, no props, no text, no watermark.
- Constant soft frontal light with no flicker and no changing shadows.

LOOP
- {loop_text}Breathing ends at the same point in its cycle as it began, at the top of an exhale."""


def negative_prompt(pose: str = "curled_side") -> str:
    common = (
        "background change, colored background, gradient background, studio backdrop, visible floor, "
        "camera movement, zoom, pan, handheld shake, cut, scene transition, second animal, human hands, "
        "jumping, morphing fur, changing markings, changing coat color, extra limbs, missing limbs, "
        "fused limbs, deformed paws, text, watermark, logo, subtitles, flicker"
    )
    if pose == "walk":
        return common + ", moving toward camera, moving away from camera, leaving center, sliding feet"
    if pose == "groom":
        return common + ", walking away, running, standing up, malformed tongue, detached paw"
    if pose == "scratch_neck":
        return common + ", walking away, running, detached hind leg, misplaced hind leg, violent shaking"
    return common + ", eyes opening, standing up, walking, head lift, sudden large movement"
