"""Run all six sessions and compare independent existing raw-field audit counts."""
from pathlib import Path
import json
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from qc.analysis import analyze

root=Path(__file__).resolve().parents[1]
inputs=(root/'samples') if (root/'samples').exists() else (root.parent/'rnx-61-66-1028')
audit_file=(root/'tests'/'audit_metrics.json') if (root/'tests'/'audit_metrics.json').exists() else (root.parent/'quality_audit_20261029'/'metrics.json')

if not inputs.exists() or not audit_file.exists():
    print("六站实测原始观测文件或审计指标未提供，跳过全量复核。")
    sys.exit(0)

audit=json.loads(audit_file.read_text(encoding='utf-8'))['observations']
results=[]
for station,expected in audit.items():
    result,pairs=analyze(inputs/f'{station}_20261028.26o',inputs/f'{station}_61028.nav')
    s=result['summary']
    assert s['epochs']==expected['epoch_count'],station
    assert s['valid_epochs']==expected['valid_epoch_count'],station
    assert s['phase_observations']==expected['numeric_phase_observations'],station
    assert s['lli_phase_fields']==expected['lli_bit0_with_numeric_phase'],station
    assert s['lli_blank_fields']==expected['lli_bit0_without_numeric_phase'],station
    assert abs(s['mean_satellites']-expected['numeric_satellite_mean'])<1e-10,station
    for signal in result['signals']:
        key=signal['id'][0]+'_S'+signal['id'][2:]
        if key in expected['cn0_statistics']:
            original=expected['cn0_statistics'][key]
            assert signal['snr']['n']==original['n'],(station,key)
            assert abs(signal['snr']['mean']-original['mean'])<1e-10,(station,key)
    assert len({(ev['epoch'],ev['sat']) for ev in result['events']})==s['events']
    assert 0<s['pdop']['n']<=s['epochs']
    # Strict JSON serialization rejects NaN or infinity anywhere in a result.
    json.dumps(result,allow_nan=False)
    json.dumps(pairs,allow_nan=False)
    row={'station':station,'status':'PASS','epochs':s['epochs'],'valid_epochs':s['valid_epochs'],
         'lli_valid':s['lli_phase_fields'],'lli_blank':s['lli_blank_fields'],
         'cn0_samples':s['cn0']['n'],'pdop_mean':s['pdop']['mean'],'pdop_epochs':s['pdop']['n'],
         'events':s['events'],'mp1_rms':s['mp1']['rms'],'mp1_n':s['mp1']['n'],
         'missing_geometry':s['missing_geometry'],'seconds':result['elapsed_seconds'],
         'obs_sha256':result['files']['obs']['sha256'],'nav_sha256':result['files']['nav']['sha256']}
    results.append(row)
    print(station,'PASS',s['epochs'],'epochs; PDOP',round(s['pdop']['mean'],4),flush=True)
(root/'outputs').mkdir(exist_ok=True)
(root/'outputs'/'sample_validation.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
print('All six sessions match the independent raw-observation audit.')
