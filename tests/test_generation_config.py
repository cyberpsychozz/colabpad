"""ALIVE works with the shipped OpenRouter config, with or without a description."""

import pytest

from tests.animationkit import split_performance
from tests.test_alive import LASSO, wait
from tests.test_alive import make as make
from tests.test_choreography import performance
from tests.test_vlm import COW, adapter, reply


@pytest.mark.parametrize("description", ["", "помаши рукой"])
def test_shipped_openrouter_config_completes_alive(make, description):
    requests = []

    def handler(request, body, n):
        requests.append(body)
        return reply(split_performance(performance())[n - 1] if description else COW)

    vlm, spent, models = adapter(handler)
    world, client = make(vlm)
    seqs = world.draw()
    response = client.post(
        "/api/canvases/world/alive",
        json={"lasso": LASSO, "description": description},
    )
    assert response.status_code == 202
    status = wait(client, response.json()["jobId"])
    assert status["status"] == "spawned"
    assert world.app.state.repo.static_seqs("world", seqs) == set()
    assert client.get("/api/me").json()["quota"]["used"] == 1
    assert len(requests) == (2 if description else 1) and spent
    assert requests[0]["max_tokens"] == (8192 if description else 700)


def test_configured_description_limit_rejects_before_provider_and_allows_boundary(make):
    vlm, spent, models = adapter(lambda request, body, n: reply(split_performance(performance())[n - 1]))
    world, client = make(vlm)
    seqs = world.draw()
    world.app.state.config.vlm["generation"]["description_chars"] = 4

    response = client.post(
        "/api/canvases/world/alive",
        json={"lasso": LASSO, "description": "прыгай"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
    assert not models and not spent
    assert not world.app.state.alive.locks.owner
    assert world.app.state.repo.static_seqs("world", seqs) == set(seqs)
    assert client.get("/api/me").json()["quota"]["used"] == 0

    response = client.post(
        "/api/canvases/world/alive",
        json={"lasso": LASSO, "description": "  маши  "},
    )
    assert response.status_code == 202
    assert wait(client, response.json()["jobId"])["status"] == "spawned"
