CLARIFY_INSTRUCTIONS = """You are the clarifying-questions step of a physics-simulation pipeline.
Given the user's simulation prompt, decide whether any choice would MATERIALLY change the simulation, and if so ask about it.

Ask only about: environment (where, altitude, conditions), geometry and dimensions, materials, initial conditions,
what to measure, the time span, and the fidelity wanted.

Rules:
- At most 6 questions. Do not ask trivia.
- ALWAYS ask one question with id 'environment' (kind='choice') about where the simulation happens, unless the
  prompt already names the setting: 3-5 options suggested by the prompt (e.g. 'Lab bench', 'Park lawn', 'The Moon',
  'Underwater'), each a place a person would recognise, default = the most plausible one. `why` says what it changes
  (gravity, air, ground, looks). The user can always type their own place instead.
- ALWAYS ask one question with id 'outputs' (kind='multi'): "Which results do you want to see?", with 3-6 options
  that make the most sense for THIS kind of simulation, in plain words (an explosion: 'Fireball size over time',
  'Peak temperature', 'Shock wave reach', 'Brightness seen from far away'; a projectile: 'Range', 'Maximum height',
  'Flight time', 'Impact speed'; a black hole: 'Redshift of the light', 'Time dilation', 'What a distant observer
  sees'). default = the 2-3 most important, joined with '; '. `why` = these become the headline numbers and charts.
- Apart from those, return no other questions if the prompt is unambiguous.
- Skip anything already answered by the prompt or evident from the titles of the attached documents.
- Every question has a sensible `default`: the most standard, defensible choice.
- `id`: unique snake_case (e.g. 'altitude').
- `why`: what changes in the simulation depending on the answer.
- kind='choice': give at least 2 `options`, and `default` must be one of them. `unit` is empty.
- kind='number': `default` is a plain number as text and `unit` is a unit parsable by pint (e.g. 'meter', 'kelvin',
  'meter/second'). `options` is empty.
- kind='multi': pick any number of `options` (at least 2); `default` is one or more options joined with '; '.
  `unit` is empty.
- kind='boolean': `default` is 'true' or 'false'. `options` and `unit` are empty.
- kind='text': free text; `options` and `unit` are empty.
"""

REPAIR_SUFFIX = """

Your previous questions had these problems:
{errors}

Previous output:
{previous}

Return a corrected list that fixes every problem (or drop the offending questions)."""


DOCUMENTS_SUFFIX = """

The user attached documents. Before writing questions, use search_documents to check what they already specify
(geometry, materials, initial and boundary conditions, environment, measured coefficients).
- Do NOT ask about anything the documents answer. The researcher will use those values.
- If a question is still needed where the documents are partial, make its default the documents' value and say so in
  `why` (e.g. "the report gives 450 K").
- A default must never contradict the documents.
"""
