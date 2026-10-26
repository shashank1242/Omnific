"""Intake prompts. Attachments become plain text that every later agent reads as part of the prompt."""

IMAGE_PROMPT = """\
This image is a user's sketch, photo or diagram of a physical system they want simulated. Describe the \
setup so that a physicist could build the simulation from your text alone:

1. The objects or bodies, and how they are connected or arranged (supports, pivots, ramps, strings, walls, fluids).
2. Every labelled quantity, copied exactly as written with its unit (e.g. "m = 2 kg", "θ = 30°", "v₀ = 5 m/s"). \
Read handwriting carefully; if a label is illegible, say "illegible" rather than guessing.
3. Arrows: what each one most likely means (force, velocity, acceleration, gravity, flow) and its direction.
4. Quantities that are drawn but not labelled (angles, relative sizes, positions): give a rough value and mark \
it "estimated".
5. Materials, surfaces or environment if shown or written (e.g. "rough surface", "water", "vacuum").
6. Any written question or goal (e.g. "how far does it slide?").

Do not invent numbers that are neither written nor clearly drawn. If the image is not a physical setup (a logo, a \
screenshot of text, a blank page), say so in one sentence and transcribe any text in it.
Answer in plain text, no preamble."""

NO_TYPED_PROMPT = "(No typed prompt: the attachments below describe the simulation.)"

PRECEDENCE = (
    "If the typed prompt and an attachment disagree, the typed prompt wins. Values marked \"estimated\" were "
    "read off a picture, not stated by the user; state them as assumptions."
)
