from __future__ import annotations

from household_supply.application import JsonApiResponse
from household_supply.domain import CatalogSnapshot, Item, Quantity, SKU
from household_supply.web import HouseholdWebJsonApi


class StubApi:
    def __init__(self, response: JsonApiResponse) -> None:
        self.response = response

    def handle(self, method: str, path: str, payload=None) -> JsonApiResponse:
        return self.response


def make_catalog() -> CatalogSnapshot:
    milk = Item("milk", "Молоко", "dairy")
    return CatalogSnapshot(
        (SKU("milk-1l", milk, "Молоко 1 л", Quantity("1", "l")),),
        (),
    )


def test_web_humanizes_missing_market_coverage_for_new_plan() -> None:
    response = JsonApiResponse(
        201,
        {
            "plan": {
                "plan_id": "plan-1",
                "result": {
                    "status": "infeasible",
                    "minimum_required_cost": None,
                    "infeasibility_reasons": [
                        "no available compatible offer can cover required item: milk"
                    ],
                    "explanation": [
                        "planning stopped because required market coverage is missing"
                    ],
                },
            }
        },
    )
    api = HouseholdWebJsonApi(StubApi(response), make_catalog())

    result = api.handle("POST", "/plans", {}).body["plan"]["result"]

    message = result["infeasibility_reasons"][0]
    assert "Молоко" in message
    assert "обновить цены" in message
    assert "no available compatible offer" not in message


def test_web_humanizes_missing_market_coverage_for_stored_plan() -> None:
    response = JsonApiResponse(
        200,
        {
            "plan_id": "plan-1",
            "result": {
                "status": "infeasible",
                "minimum_required_cost": None,
                "infeasibility_reasons": [
                    "no available compatible offer can cover required item: milk"
                ],
                "explanation": [],
            },
        },
    )
    api = HouseholdWebJsonApi(StubApi(response), make_catalog())

    result = api.handle("GET", "/plans/plan-1").body["result"]

    assert "Молоко" in result["infeasibility_reasons"][0]


def test_web_keeps_structured_minimum_budget_result_unchanged() -> None:
    reason = "minimum required purchase cost exceeds budget by 20 KGS"
    response = JsonApiResponse(
        201,
        {
            "plan": {
                "result": {
                    "status": "infeasible",
                    "minimum_required_cost": {"amount": "120", "currency": "KGS"},
                    "infeasibility_reasons": [reason],
                    "explanation": [],
                }
            }
        },
    )
    api = HouseholdWebJsonApi(StubApi(response), make_catalog())

    result = api.handle("POST", "/plans", {}).body["plan"]["result"]

    assert result["minimum_required_cost"] == {"amount": "120", "currency": "KGS"}
    assert result["infeasibility_reasons"] == [reason]


def test_web_does_not_expose_unknown_internal_infeasibility_reason() -> None:
    response = JsonApiResponse(
        200,
        {
            "result": {
                "status": "infeasible",
                "minimum_required_cost": None,
                "infeasibility_reasons": ["internal planner diagnostic xyz"],
                "explanation": [],
            }
        },
    )
    api = HouseholdWebJsonApi(StubApi(response), make_catalog())

    result = api.handle("GET", "/plans/plan-1").body["result"]

    message = result["infeasibility_reasons"][0]
    assert "изменить обязательные продукты" in message
    assert "internal planner diagnostic xyz" not in message
