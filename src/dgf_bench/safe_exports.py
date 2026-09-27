"""Non-destructive exports with disjoint paths and an evidence whitelist."""
from pathlib import Path
import json
import shutil
import tempfile

def export_case(source, target, phase=None):
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError('Source and output must be disjoint directories')
    if target.exists():
        raise FileExistsError('Output already exists; choose a new directory')
    if not source.is_dir():
        raise FileNotFoundError(source)
    graph = json.loads((source / '02_evidence_graph.json').read_text(encoding='utf-8'))
    allowed = None
    if phase is not None:
        allowed = set(json.loads((source / '05_phase_visibility.json').read_text(encoding='utf-8'))[phase])
    inputs = ['00_project_context.json', '01_route_manifest.json', '02_evidence_graph.json',
              '03_tool_schemas.json', '04_gate_contracts.json', '05_phase_visibility.json',
              '06_authorization_registry.json', 'agent_submission_template.json', 'README_CASE.md']
    for node in graph['nodes']:
        if node.get('public_status') != 'AVAILABLE' or (allowed is not None and node['evidence_id'] not in allowed):
            continue
        path = (source / node['path']).resolve()
        if source not in path.parents or path.name in {'99_hidden_ground_truth.json', 'tool_trace.jsonl', 'environment_state.json'}:
            raise ValueError('Unsafe evidence path')
        inputs.append(path.relative_to(source).as_posix())
        if path.suffix == '.svg' and path.with_suffix('.png').exists():
            inputs.append(path.with_suffix('.png').relative_to(source).as_posix())
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dgf-export-', dir=target.parent) as temporary:
        staging = Path(temporary) / 'case'; staging.mkdir()
        for name in inputs:
            path = (source / name).resolve()
            if source not in path.parents:
                raise ValueError('Unsafe export path')
            if path.is_file():
                destination = staging / name; destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
        if phase:
            (staging / 'PHASE.txt').write_text(phase, encoding='utf-8')
        staging.rename(target)
    return target
