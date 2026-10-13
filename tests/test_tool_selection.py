from app.tools.hrms import pick_tool_for
from app.types import UserContext


USER = UserContext(slack_user_id="U_X", employee_id="E1024", acl_groups=["all_employees"])


def test_leave_question_routes_to_balance():
    name, args = pick_tool_for("How many sick leaves do I have left?", USER)
    assert name == "get_leave_balance"
    assert args["employee_id"] == "E1024"


def test_join_date_routes_to_profile():
    name, args = pick_tool_for("when did I join the company?", USER)
    assert name == "get_employee_profile"


def test_ticket_creation():
    name, args = pick_tool_for("Please raise a ticket about my laptop", USER)
    assert name == "create_hr_ticket"


def test_unknown_question_returns_none():
    name, args = pick_tool_for("What's the weather like?", USER)
    assert name is None


def test_no_employee_id_blocks_tools():
    anon = UserContext(slack_user_id="U_unknown", employee_id=None, acl_groups=["all_employees"])
    name, args = pick_tool_for("How many leaves do I have?", anon)
    assert name is None
