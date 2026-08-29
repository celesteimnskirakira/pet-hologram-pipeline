"""Prompt construction for the two generation steps.

Identity preservation is the whole game here. The still prompt is built from a
structured trait description extracted from the upload, so the image model gets
explicit constraints instead of a vague "keep it similar".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

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


def video_prompt(traits: PetTraits, pose: str = "curled_side", loop_hint: bool = True) -> str:
    """Step 3 prompt: sleeping loop, minimal motion so head/tail frames match."""
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


def negative_prompt() -> str:
    return (
        "background change, colored background, gradient background, studio backdrop, visible floor, "
        "camera movement, zoom, pan, handheld shake, cut, scene transition, second animal, human hands, "
        "eyes opening, standing up, walking, jumping, head lift, morphing fur, changing markings, "
        "changing coat color, extra limbs, deformed paws, text, watermark, logo, subtitles, flicker"
    )
