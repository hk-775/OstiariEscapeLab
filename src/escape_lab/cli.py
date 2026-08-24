"""Command-line interface for Escape Lab."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from escape_lab.agents import (
    AxonLLMAgentFactory,
    AxonLLMConfig,
    ScriptedAgentFactory,
)
from escape_lab.disclosure import (
    DISCLOSURE_STATUSES,
    append_disclosure_status,
    verify_disclosure_ledger,
)
from escape_lab.evidence import verify_evidence
from escape_lab.experiment import replay_run, run_experiment
from escape_lab.gate import load_baseline, run_gate
from escape_lab.isolation import (
    docker_preflight,
    hardened_docker_command,
    probe_docker_image,
)
from escape_lab.models import ControlProfile
from escape_lab.orchestrator import (
    RunOrchestrator,
    default_project_root,
    default_registry,
)
from escape_lab.resources import resource_text
from escape_lab.sandbox import (
    DockerSandboxConfig,
    DockerSandboxFactory,
    SyntheticRangeFactory,
)
from escape_lab.util import atomic_write_text, is_within


def _profiles(value: str) -> list[ControlProfile]:
    profiles = [ControlProfile.parse(item.strip()) for item in value.split(",") if item.strip()]
    if not profiles:
        raise argparse.ArgumentTypeError("At least one profile is required")
    return profiles


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="escape-lab",
        description="Provider-neutral agent-containment regression lab",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="Project root containing an override scenarios/catalog.json",
    )
    parser.add_argument(
        "--artifacts-root",
        type=Path,
        default=None,
        help="Override the artifacts output directory",
    )
    parser.add_argument(
        "--backend",
        choices=["reference", "ostiari"],
        default="reference",
        help="Control backend for C2-C4 evaluation",
    )
    parser.add_argument(
        "--ostiari-src",
        type=Path,
        default=None,
        help="Path to an Ostiari source checkout for --backend ostiari",
    )
    parser.add_argument(
        "--agent",
        choices=["scripted", "axonllm"],
        default="scripted",
        help="Agent adapter used to propose scenario tool calls",
    )
    parser.add_argument(
        "--axonllm-mode",
        choices=["fixture", "live"],
        default="live",
        help="Use the offline loopback fixture or real AxonLLM provider routes",
    )
    parser.add_argument(
        "--axonllm-src",
        type=Path,
        default=None,
        help="Path to an AxonLLM source checkout",
    )
    parser.add_argument(
        "--axonllm-models",
        type=Path,
        default=None,
        help="AxonLLM models YAML for live mode",
    )
    parser.add_argument(
        "--axonllm-providers",
        type=Path,
        default=None,
        help="AxonLLM providers YAML for live mode",
    )
    parser.add_argument(
        "--axonllm-pricing",
        type=Path,
        default=None,
        help="Optional AxonLLM pricing YAML for live mode",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="AxonLLM logical model for live mode",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="Optional preferred AxonLLM provider",
    )
    parser.add_argument(
        "--max-agent-turns",
        type=int,
        default=20,
        help="Maximum AxonLLM model turns per scenario",
    )
    parser.add_argument(
        "--range-backend",
        choices=["synthetic", "docker", "gvisor"],
        default="synthetic",
        help="Execution boundary for range state and tool effects",
    )
    parser.add_argument(
        "--sandbox-image",
        default=None,
        help="OCI image for docker/gvisor; defaults to environment.image",
    )
    parser.add_argument(
        "--sandbox-runtime",
        default=None,
        help="Docker runtime override; gvisor defaults to runsc",
    )
    parser.add_argument(
        "--docker-binary",
        default="docker",
        help="Docker-compatible CLI used for isolated ranges",
    )
    parser.add_argument(
        "--sandbox-memory",
        default="256m",
        help="Memory limit for an isolated range",
    )
    parser.add_argument(
        "--sandbox-cpus",
        default="0.5",
        help="CPU limit for an isolated range",
    )
    parser.add_argument(
        "--sandbox-pids",
        type=int,
        default=64,
        help="Process limit for an isolated range",
    )
    parser.add_argument(
        "--sandbox-rpc-timeout",
        type=float,
        default=10.0,
        help="Seconds before a non-responsive range is force-removed",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser(
        "init",
        help="Create a reviewed release-gate baseline in a project",
    )
    init.add_argument("directory", nargs="?", type=Path, default=Path.cwd())
    init.add_argument("--force", action="store_true")

    subparsers.add_parser("validate", help="Validate scenario contracts and MVP acceptance shape")
    subparsers.add_parser("list", help="List registered scenarios")

    run_parser = subparsers.add_parser("run", help="Run one scenario")
    run_parser.add_argument("scenario")
    run_parser.add_argument("--profile", default="C4")
    run_parser.add_argument("--seed", type=int, default=1)
    run_parser.add_argument("--allow-c0", action="store_true")

    compare = subparsers.add_parser("compare", help="Run paired control-profile trials")
    compare.add_argument("scenarios", nargs="+")
    compare.add_argument("--profiles", type=_profiles, default=_profiles("C1,C2,C3,C4"))
    compare.add_argument("--trials", type=int, default=1)
    compare.add_argument("--seed", type=int, default=1)
    compare.add_argument("--allow-c0", action="store_true")

    demo = subparsers.add_parser("demo", help="Run the private three-scenario MVP demo")
    demo.add_argument("--trials", type=int, default=1)
    demo.add_argument("--seed", type=int, default=1)

    gate = subparsers.add_parser(
        "gate",
        help="Run a baseline and fail when containment regresses",
    )
    gate.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="Baseline JSON; defaults to .escape-lab/baseline.json or the packaged baseline",
    )
    gate.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Write gate.json, gate.md, gate.html, and junit.xml here",
    )

    replay = subparsers.add_parser("replay", help="Replay a frozen run")
    replay.add_argument("run_dir", type=Path)
    replay.add_argument("--allow-version-drift", action="store_true")

    verify = subparsers.add_parser("verify", help="Verify run evidence and disclosure ledgers")
    verify.add_argument("run_dir", type=Path)

    kill = subparsers.add_parser("kill", help="Request independent termination of a running run")
    kill.add_argument("run_id")
    kill.add_argument("--reason", default="operator request")

    disclosure = subparsers.add_parser("disclosure", help="Append a disclosure workflow status")
    disclosure.add_argument("run_dir", type=Path)
    disclosure.add_argument("status", choices=sorted(DISCLOSURE_STATUSES))
    disclosure.add_argument("--note", default="")
    disclosure.add_argument("--actor", default="lab-owner")

    preflight = subparsers.add_parser(
        "preflight",
        help="Check registry and optional Docker T2 posture",
    )
    preflight.add_argument("--docker-image")
    preflight.add_argument("--probe-docker", action="store_true")

    clean = subparsers.add_parser("clean-artifacts", help="Delete generated artifacts")
    clean.add_argument("--yes", action="store_true", help="Confirm deletion")
    return parser


def _orchestrator(args: argparse.Namespace) -> tuple[Path, RunOrchestrator]:
    project_root = (args.project_root or default_project_root()).resolve()
    registry = default_registry(
        project_root,
        require_project_catalog=args.project_root is not None,
    )
    if args.agent == "axonllm":
        agent_factory = AxonLLMAgentFactory(
            AxonLLMConfig(
                source=args.axonllm_src,
                mode=args.axonllm_mode,
                models=args.axonllm_models,
                providers=args.axonllm_providers,
                pricing=args.axonllm_pricing,
                model=args.model,
                preferred_provider=args.provider,
                max_turns=args.max_agent_turns,
            )
        )
    else:
        agent_factory = ScriptedAgentFactory()
    if args.range_backend == "synthetic":
        range_factory = SyntheticRangeFactory()
    else:
        runtime = args.sandbox_runtime
        if args.range_backend == "gvisor" and runtime is None:
            runtime = "runsc"
        range_factory = DockerSandboxFactory(
            DockerSandboxConfig(
                image=args.sandbox_image,
                runtime=runtime,
                docker_binary=args.docker_binary,
                memory=args.sandbox_memory,
                cpus=args.sandbox_cpus,
                pids_limit=args.sandbox_pids,
                rpc_timeout_seconds=args.sandbox_rpc_timeout,
            )
        )
    orchestrator = RunOrchestrator(
        project_root=project_root,
        registry=registry,
        artifacts_root=args.artifacts_root,
        control_backend=args.backend,
        ostiari_source=args.ostiari_src,
        agent_factory=agent_factory,
        range_factory=range_factory,
    )
    return project_root, orchestrator


def _init_project(directory: Path, *, force: bool) -> Path:
    destination = directory.resolve() / ".escape-lab" / "baseline.json"
    if destination.exists() and not force:
        raise FileExistsError(
            f"Baseline already exists: {destination}; use --force to replace it"
        )
    atomic_write_text(
        destination,
        resource_text("baselines/first-product.json"),
    )
    return destination


def _baseline_path(argument: Path | None) -> Path | None:
    if argument is not None:
        return argument.resolve()
    local = Path.cwd() / ".escape-lab" / "baseline.json"
    return local.resolve() if local.is_file() else None


def _append_github_summary(report_path: Path) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    with Path(summary).open("a", encoding="utf-8") as handle:
        handle.write(report_path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            destination = _init_project(args.directory, force=args.force)
            print(f"Created Escape Lab release baseline: {destination}")
            print("Run: escape-lab gate")
            return 0

        project_root, orchestrator = _orchestrator(args)
        if args.command == "validate":
            issues = orchestrator.registry.validate_mvp()
            if issues:
                for issue in issues:
                    print(f"ERROR: {issue}", file=sys.stderr)
                return 1
            print(
                f"Validated {len(orchestrator.registry.list())} scenarios; "
                "taxonomy, manifests, and T2 coverage satisfy the MVP contract."
            )
            return 0

        if args.command == "list":
            print("ID   Tier  Mode  Version  Title")
            for scenario in orchestrator.registry.list():
                print(
                    f"{scenario.scenario_id:<4} {scenario.tier:<5} "
                    f"{scenario.failure_mode:<5} {scenario.version:<8} {scenario.title}"
                )
            return 0

        if args.command == "run":
            profile = ControlProfile.parse(args.profile)
            result = orchestrator.run(
                args.scenario,
                profile=profile,
                seed=args.seed,
                allow_c0=args.allow_c0,
            )
            print(
                f"{result.run_id}: {result.outcome.value} "
                f"({result.validity.value}) -> {result.artifact_dir}"
            )
            return 0 if result.validity.value == "valid" else 2

        if args.command == "compare":
            output, _results, summary = run_experiment(
                orchestrator,
                scenario_ids=[item.upper() for item in args.scenarios],
                profiles=args.profiles,
                trials=args.trials,
                base_seed=args.seed,
                allow_c0=args.allow_c0,
            )
            for profile, values in summary["profiles"].items():
                print(
                    f"{profile}: CFR={values['containment_failure_rate']:.3f}, "
                    f"capability={values['residual_task_capability']:.3f}, "
                    f"valid={values['valid_trials']}"
                )
            print(f"Comparison report: {output}")
            return 0

        if args.command == "demo":
            output, _results, summary = run_experiment(
                orchestrator,
                scenario_ids=["S03", "S06", "S09"],
                profiles=[
                    ControlProfile.C1,
                    ControlProfile.C2,
                    ControlProfile.C3,
                    ControlProfile.C4,
                ],
                trials=args.trials,
                base_seed=args.seed,
                allow_c0=False,
            )
            for profile, values in summary["profiles"].items():
                print(
                    f"{profile}: escapes={values['escapes']}/"
                    f"{values['valid_trials']}, "
                    f"capability={values['residual_task_capability']:.3f}"
                )
            print(f"Demo evidence package: {output}")
            return 0

        if args.command == "gate":
            baseline = load_baseline(_baseline_path(args.baseline))
            gate_result = run_gate(
                orchestrator,
                baseline,
                output_dir=(
                    args.output_dir.resolve()
                    if args.output_dir is not None
                    else None
                ),
            )
            _append_github_summary(gate_result.output_dir / "gate.md")
            print(
                f"Release gate: {gate_result.report['status']} -> "
                f"{gate_result.output_dir}"
            )
            for reason in gate_result.report["reasons"]:
                print(f"REGRESSION: {reason}", file=sys.stderr)
            return 0 if gate_result.passed else 10

        if args.command == "replay":
            result, check = replay_run(
                orchestrator,
                args.run_dir.resolve(),
                allow_version_drift=args.allow_version_drift,
            )
            print(
                f"Replay {result.run_id}: reproducible={check['reproducible']} "
                f"-> {result.artifact_dir}"
            )
            return 0 if check["reproducible"] else 3

        if args.command == "verify":
            evidence = verify_evidence(args.run_dir.resolve() / "events.jsonl")
            disclosure = verify_disclosure_ledger(
                args.run_dir.resolve() / "disclosure.jsonl"
            )
            print(
                f"evidence_valid={evidence['valid']} "
                f"events={evidence['event_count']} "
                f"disclosure_valid={disclosure['valid']}"
            )
            return 0 if evidence["valid"] and disclosure["valid"] else 4

        if args.command == "kill":
            control_dir = orchestrator.artifacts_root / "control"
            control_dir.mkdir(parents=True, exist_ok=True)
            kill_path = control_dir / f"{args.run_id}.kill"
            atomic_write_text(kill_path, f"{args.reason}\n")
            print(f"Kill request written: {kill_path}")
            return 0

        if args.command == "disclosure":
            record = append_disclosure_status(
                args.run_dir.resolve(),
                status=args.status,
                note=args.note,
                actor=args.actor,
            )
            print(
                f"Disclosure status #{record['sequence']}: "
                f"{record['status']} ({record['record_hash']})"
            )
            return 0

        if args.command == "preflight":
            issues = orchestrator.registry.validate_mvp()
            if issues:
                for issue in issues:
                    print(f"ERROR: {issue}", file=sys.stderr)
                return 1
            status = docker_preflight(args.docker_binary)
            requested_runtime = args.sandbox_runtime
            if args.range_backend == "gvisor" and requested_runtime is None:
                requested_runtime = "runsc"
            runtime_ready = (
                requested_runtime is None
                or requested_runtime in status.runtimes
            )
            print(
                f"registry=valid docker_available={status.available} "
                f"daemon_reachable={status.daemon_reachable} "
                f"server={status.server_version or '-'} "
                f"runtimes={','.join(status.runtimes) or '-'}"
            )
            if requested_runtime:
                print(
                    f"requested_runtime={requested_runtime} "
                    f"runtime_ready={runtime_ready}"
                )
            if status.error:
                print(f"docker_error={status.error}")
            if args.docker_image:
                print("hardened_command:")
                print(
                    " ".join(
                        hardened_docker_command(
                            args.docker_image,
                            runtime=requested_runtime,
                            docker_binary=args.docker_binary,
                        )
                    )
                )
                if args.probe_docker:
                    probe = probe_docker_image(
                        args.docker_image,
                        runtime=requested_runtime,
                        docker_binary=args.docker_binary,
                    )
                    print(
                        f"docker_probe_success={probe['success']} "
                        f"returncode={probe['returncode']}"
                    )
                    if probe["stdout"]:
                        print(probe["stdout"])
                    if probe["stderr"]:
                        print(probe["stderr"], file=sys.stderr)
                    return 0 if probe["success"] and runtime_ready else 5
            return (
                0
                if (
                    (not args.probe_docker or status.daemon_reachable)
                    and runtime_ready
                )
                else 5
            )

        if args.command == "clean-artifacts":
            artifacts = orchestrator.artifacts_root.resolve()
            if not args.yes:
                print("Refusing to delete artifacts without --yes", file=sys.stderr)
                return 2
            if not is_within(artifacts, project_root) or artifacts == project_root:
                print("Refusing to delete an unsafe artifacts path", file=sys.stderr)
                return 2
            if artifacts.exists():
                shutil.rmtree(artifacts)
            print(f"Removed generated artifacts: {artifacts}")
            return 0
    except (KeyError, RuntimeError, ValueError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
