"""What an automatic plan run tells the model when it asks for a repair.

The step-plan runner sends these with a redraft: a final output that asked
questions, a file that failed its check, a weak draft or final output. The
thresholds decide which drafts are repaired and which pause for a person.
"""

from __future__ import annotations

NO_QUESTIONS = (
    "Do not ask questions or offer options: write the deliverable now, marked as a "
    "deliverable, from the plan's ask and what is already known. Use your tools and "
    "skills if the step calls for them; do not imitate their output by hand."
)

FIX_FILE = (
    "The app checked the file you gave and found: {problems}. Fix it (rebuild the file with "
    "your tools if needed), then give its absolute path again as the artifact."
)
REVIEW_FEEDBACK = (
    "Review feedback can be wrong and is not a new user requirement. Verify each finding "
    "against the original request, accepted follow-ups, the current step instruction when "
    "applicable, and the current result. Preserve the user's constraints and do not invent "
    "facts, requirements, or actions to satisfy a mistaken finding. "
)
FIX_WEAK = (
    "The step was scored {score}/100. The reviewer reported potential gaps or contradictions: "
    "{gaps}. "
    f"{REVIEW_FEEDBACK}"
    "Correct genuine issues in the artifact itself, not just the explanation. Use your tools "
    "only when appropriate and allowed for the requested work, and check its actual structure "
    "and counts against each stated constraint before giving the improved artifact. It must "
    "remain this step's own result, not a section that belongs to another step."
)
FIX_FINAL_GAPS = (
    " The final assessment reported potential gaps or contradictions: {gaps}. "
    f"{REVIEW_FEEDBACK}"
    "Correct genuine issues in the final deliverable itself while preserving approved work. "
    "Do not invent evidence or take unrelated actions to satisfy the review."
)
WEAK_BELOW = 60
HOLD_BELOW = 50
FIX_FINAL_PATH = (
    " The app checked the paths in your previous final output and found: {problems}. "
    "The files the steps really built are: {built}. Give exactly those paths, with a short summary and how to open them; do not paste or rewrite the file's content."
)
