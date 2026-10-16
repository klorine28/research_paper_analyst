"""
Guided navigation: the phase-grouped numbered sidebar, the step bar, Previous/Next.

The sidebar lists every registered page under its numbered step, with the steps
grouped into phases; the step bar on every page shows each step, completed ones
ticked and the current one highlighted; Previous/Next name the adjacent step
(issue #76). All three are buttons that only switch the page, so free
navigation stays: nothing here locks a step. Completion comes from
`guided_flow`; the steps' pages come from the page registry.
"""

import streamlit as st

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.guided_flow import (
  PHASES,
  STEPS,
  FlowProgress,
  Step,
  step_label,
)
from research_gap_dashboard.dashboard.page_registry import (
  PAGES,
  first_page_title,
  pages_for_step,
)
from research_gap_dashboard.dashboard.views.session_keys import NAV_PAGE_KEY

PREVIOUS_BUTTON_KEY = "nav-previous"
NEXT_BUTTON_KEY = "nav-next"


def nav_button_key(title: str) -> str:
  """Return the widget key of a page's sidebar button."""
  return f"nav-page-{title}"


def step_bar_key(step: Step) -> str:
  """Return the widget key of a step's button on the step bar."""
  return f"step-bar-{step.value}"


def _open_page(title: str) -> None:
  st.session_state[NAV_PAGE_KEY] = title


def _marked_label(step: Step, progress: FlowProgress) -> str:
  mark = text.STEP_DONE_MARK if step in progress.completed else ""
  return mark + step_label(step)


def render_sidebar_nav(current: str, progress: FlowProgress) -> None:
  """Render the Home link and every page under its numbered step and phase."""
  home = PAGES[0].title
  st.sidebar.button(
    text.NAV_HOME_LINK,
    key=nav_button_key(home),
    type="primary" if current == home else "secondary",
    width="stretch",
    on_click=_open_page,
    args=(home,),
  )
  for phase, steps in PHASES:
    st.sidebar.caption(text.PHASE_NAMES[phase].upper())
    for step in steps:
      st.sidebar.markdown(f"**{_marked_label(step, progress)}**")
      for page in pages_for_step(step):
        st.sidebar.button(
          page.title,
          key=nav_button_key(page.title),
          type="primary" if current == page.title else "secondary",
          width="stretch",
          on_click=_open_page,
          args=(page.title,),
        )


def render_step_bar(current: Step | None, progress: FlowProgress) -> None:
  """Render the seven steps, completed ones ticked and the current one highlighted."""
  for column, step in zip(st.columns(len(STEPS)), STEPS, strict=True):
    column.button(
      _marked_label(step, progress),
      key=step_bar_key(step),
      type="primary" if step == current else "secondary",
      help=text.STEP_BAR_HELP_DONE
      if step in progress.completed
      else text.STEP_BAR_HELP_TODO,
      width="stretch",
      on_click=_open_page,
      args=(first_page_title(step),),
    )


def render_previous_next(current: Step | None) -> None:
  """Render buttons to the adjacent steps; before Scope comes the Home screen."""
  index = STEPS.index(current) if current is not None else -1
  previous_col, _, next_col = st.columns([2, 3, 2])
  if current is not None:
    previous = STEPS[index - 1] if index > 0 else None
    target = first_page_title(previous) if previous else PAGES[0].title
    name = step_label(previous) if previous else text.PAGE_HOME
    previous_col.button(
      text.NAV_PREVIOUS.format(step=name),
      key=PREVIOUS_BUTTON_KEY,
      width="stretch",
      on_click=_open_page,
      args=(target,),
    )
  if index + 1 < len(STEPS):
    following = STEPS[index + 1]
    next_col.button(
      text.NAV_NEXT.format(step=step_label(following)),
      key=NEXT_BUTTON_KEY,
      type="primary",
      width="stretch",
      on_click=_open_page,
      args=(first_page_title(following),),
    )
