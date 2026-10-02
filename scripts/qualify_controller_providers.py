"""Bounded real-provider smoke using public synthetic data, never a personal screen."""

import argparse
import asyncio
import json
import platform
import tomllib
from pathlib import Path
from time import monotonic, time

from assistant.audio.contracts import Language, Mode, ProviderUnavailable
from assistant.controller.policy import Grants, Turns
from assistant.controller.secrets import Credentials
from assistant.providers.llm.chat import ModelContext
from assistant.providers.registry import create_models


async def run(args):
    if not args.allow_cloud_fixture:
        raise SystemExit(
            "Pass --allow-cloud-fixture to authorize only public synthetic fixture data."
        )
    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    turns, grants = Turns(), Grants()
    scope = "provider-public-fixture"
    turn = turns.begin(Language.ENGLISH, Mode.ACT, scope)
    decider, planner, chat = create_models(config, Credentials())
    ids = []
    for provider in (decider, chat):
        if provider and provider.info.locality == "cloud":
            print(
                f"Public fixture disclosure: {provider.info.provider} / {provider.info.model}; "
                "expires in 5 minutes."
            )
            grant = grants.issue(
                kind="cloud",
                scope=scope,
                expires_at=time() + 300,
                provider=provider.info.provider,
                model=provider.info.model,
                fields=frozenset({"goal", "screen_text", "source_text", "preferences"}),
            )
            ids.append(grant.grant_id)
    context = ModelContext(turn, scope, tuple(ids), turns, grants)
    results = []
    try:
        for repetition in range(2):
            started = monotonic()
            try:
                if decider:
                    result = await decider.evaluate(
                        {"public_fixture": "The workshop code is 007381."},
                        {
                            "route": {
                                "type": "choice",
                                "instructions": "Choose exact reading.",
                                "criteria": {
                                    "read": "Read the original code",
                                    "abstain": "Cannot choose",
                                    "reobserve": "Observe again",
                                },
                            },
                            "read_only": {
                                "type": "noul",
                                "instructions": "Is this a read-only request?",
                            },
                            "clarity": {
                                "type": "score",
                                "instructions": "Rate clarity.",
                                "criteria": ["unclear", "clear"],
                            },
                        },
                        context,
                    )
                    results.append(
                        {
                            "role": "decider",
                            "repeat": repetition,
                            "status": "passed",
                            "resolved_model": result["model"],
                            "choice": result["answers"]["route"]["choice"],
                            "confidence": result["answers"]["route"]["confidence"],
                            "elapsed_ms": round((monotonic() - started) * 1000, 2),
                            "usage": result.get("usage"),
                        }
                    )
            except Exception as error:
                results.append(
                    {
                        "role": "decider",
                        "repeat": repetition,
                        "status": "failed",
                        "reason": str(error)
                        if isinstance(error, ProviderUnavailable)
                        else type(error).__name__,
                        "elapsed_ms": round((monotonic() - started) * 1000, 2),
                    }
                )
            if planner:
                started = monotonic()
                try:
                    reply = await planner.draft(
                        "Draft one sentence giving the exact workshop code.",
                        "The workshop code is 007381.",
                        {"writing_system": "traditional", "spoken_language": "en"},
                        context,
                    )
                    results.append(
                        {
                            "role": "writer",
                            "repeat": repetition,
                            "status": "passed" if "007381" in reply.text else "failed",
                            "resolved_model": reply.model,
                            "elapsed_ms": round(reply.elapsed_ms, 2),
                            "input_tokens": reply.input_tokens,
                            "output_tokens": reply.output_tokens,
                            "cost_usd": reply.cost,
                        }
                    )
                except Exception as error:
                    results.append(
                        {
                            "role": "writer",
                            "repeat": repetition,
                            "status": "failed",
                            "reason": str(error)
                            if isinstance(error, ProviderUnavailable)
                            else type(error).__name__,
                            "elapsed_ms": round((monotonic() - started) * 1000, 2),
                        }
                    )
    finally:
        for provider in (decider, chat):
            if provider:
                await provider.close()
    report = {
        "machine_id": "windows-hacku-01" if platform.system() == "Windows" else "record-separately",
        "os": platform.platform(),
        "python": platform.python_version(),
        "fixture": "public synthetic text",
        "remote_model_cold_warm": "unknown; client repeats are not server load timings",
        "confidence_calibration": "unrun; smoke does not calibrate routing thresholds",
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if len(results) != 4 or any(result["status"] != "passed" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--output", type=Path, default=Path(".runtime/controller-providers.json"))
    parser.add_argument("--allow-cloud-fixture", action="store_true")
    asyncio.run(run(parser.parse_args()))
