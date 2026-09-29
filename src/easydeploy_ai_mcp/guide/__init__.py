"""
The EasyDeploy data-science playbook served by the ``get_started`` tool.

Hosts cache tool descriptions and server instructions, and many never show the
instructions to the model, so guidance written there reaches agents late or not
at all. Tool results are fetched live on every call. The playbook therefore
lives here, as one Markdown file per section, and ``get_started`` returns it:
the tool's name and description stay fixed while this content changes with
each release.

To update the content, edit the Markdown files and bump ``GUIDE_RELEASE``.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources

# The one edit a content update needs: bump both values together.
GUIDE_RELEASE: dict[str, str] = {"guide_version": "1.0", "updated": "2026-09-29"}

# Reading order, with the one-line summary get_started lists for each section.
SECTIONS: tuple[tuple[str, str], ...] = (
    ("overview", "Roles, the whole workflow with the tools at each step, and the hard rules."),
    ("prepare", "Frame the problem, explore the data, remove leakage, and format the training file."),
    ("split", "Split into train and test files without leakage; balancing and checks."),
    ("upload", "Get each file into EasyDeploy, dataset_type, size caps, and checks after upload."),
    ("train", "Create the model version on the train file, submit training, and read the report honestly."),
    ("validate", "Score the test file, compute holdout metrics, choose the threshold, and recommend go or no-go."),
    ("predict", "Score new data, apply the chosen threshold, and build outputs from real predictions."),
)

SECTION_NAMES: tuple[str, ...] = tuple(name for name, _ in SECTIONS)


@lru_cache(maxsize=None)
def read_section(name: str) -> str:
    """Return one section's Markdown. Raises KeyError for an unknown section."""
    if name not in SECTION_NAMES:
        raise KeyError(f"Unknown guide section {name!r}; expected one of {SECTION_NAMES}")
    return resources.files(__package__).joinpath(f"{name}.md").read_text(encoding="utf-8").strip()


def read_all() -> str:
    """Every section, in reading order."""
    return "\n\n---\n\n".join(read_section(name) for name in SECTION_NAMES)


def next_after(name: str) -> str:
    """What to read after ``name`` (a section, or ``all``)."""
    if name == "all":
        return (
            "You have every section. When you reach a step, re-read its section with "
            "get_started(section=...) rather than relying on memory."
        )
    index = SECTION_NAMES.index(name)
    if index + 1 < len(SECTION_NAMES):
        following = SECTION_NAMES[index + 1]
        return f'Read "{following}" next: call get_started with section="{following}" when you reach that step.'
    return (
        'This is the last section. For a new modeling task, start again with '
        'get_started(section="overview").'
    )
