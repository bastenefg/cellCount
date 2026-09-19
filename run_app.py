"""Desktop entry point. The scientific command-line implementation is unchanged."""
from pathlib import Path
import os
import sys


def main():
    if "--viability-smoke-test" in sys.argv:
        from desktop.viability_smoke import main as viability_smoke_main
        return viability_smoke_main(sys.argv[1:])
    if "--stack-analysis" in sys.argv or "--verify-stack-analysis" in sys.argv:
        import argparse
        parser = argparse.ArgumentParser()
        modes = parser.add_mutually_exclusive_group(required=True)
        modes.add_argument("--stack-analysis", type=Path)
        modes.add_argument("--verify-stack-analysis", type=Path)
        parser.add_argument("--log", type=Path, required=True)
        args = parser.parse_args()
        args.log.parent.mkdir(parents=True, exist_ok=True)
        os.environ["MPLBACKEND"] = "Agg"
        with args.log.open("w", encoding="utf-8", buffering=1) as stream:
            sys.stdout = sys.stderr = stream
            from desktop.analysis_3d import run_request, read_analysis_3d
            if args.stack_analysis:
                return run_request(args.stack_analysis)
            try:
                result = read_analysis_3d(args.verify_stack_analysis, verify=True)
                print(f"Verified 3D analysis: {len(result['fields'])} fields and all saved settings, masks and figures.", flush=True)
                return 0
            except Exception:
                import traceback
                traceback.print_exc()
                return 1
    if "--stack-smoke-test" in sys.argv:
        from desktop.stack_smoke import main as stack_smoke_main
        return stack_smoke_main(sys.argv[1:])
    if "--volume-analysis" in sys.argv:
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--volume-analysis", type=Path, required=True)
        args = parser.parse_args()
        os.environ["MPLBACKEND"] = "Agg"
        from desktop.volume_analysis import run_job
        return run_job(args.volume_analysis)
    if "--full-field-summary" in sys.argv:
        import argparse
        import json
        parser = argparse.ArgumentParser()
        parser.add_argument("--full-field-summary", type=Path, required=True)
        parser.add_argument("--summary-output", type=Path, required=True)
        args = parser.parse_args()
        try:
            from desktop.full_field_summary import build_full_field_summary
            result = build_full_field_summary(args.full_field_summary, args.summary_output)
            if sys.stdout is not None:
                print(json.dumps(result))
            return 0
        except Exception as exc:
            if sys.stderr is not None:
                print(str(exc), file=sys.stderr)
            return 1
    if "--preview-server" in sys.argv:
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--preview-server", type=Path, required=True)
        parser.add_argument("--parent-pid", type=int)
        args = parser.parse_args()
        from desktop.preview_server import serve
        return serve(args.preview_server, args.parent_pid)
    if "--segmentation-preview" in sys.argv:
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--segmentation-preview", type=Path, required=True)
        parser.add_argument("--preview-output", type=Path, required=True)
        args = parser.parse_args()
        from desktop.segmentation import write_preview
        return write_preview(args.segmentation_preview, args.preview_output)
    if "--leica-smoke-test" in sys.argv:
        from desktop.leica_smoke import main as leica_smoke_main
        return leica_smoke_main(sys.argv[1:])
    if "--smoke-test" in sys.argv:
        from desktop.smoke import main as smoke_main
        return smoke_main(sys.argv[1:])
    if "--pipeline" in sys.argv:
        args = sys.argv[sys.argv.index("--pipeline") + 1:]
        stream = None
        if "--log" in args:
            index = args.index("--log")
            log_path = Path(args[index + 1])
            log_path.parent.mkdir(parents=True, exist_ok=True)
            stream = log_path.open("w", encoding="utf-8", buffering=1)
            del args[index:index + 2]
            sys.stdout = sys.stderr = stream
        elif sys.stdout is None:
            sys.stdout = sys.stderr = open(os.devnull, "w")
        os.environ["MPLBACKEND"] = "Agg"
        sys.argv = ["run_pipeline.py", *args]
        try:
            from pipeline.cli import main as pipeline_main
            return pipeline_main()
        except Exception:
            import traceback
            traceback.print_exc()
            return 1
        finally:
            if stream:
                stream.flush()
    from desktop.app import main as desktop_main
    return desktop_main()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
