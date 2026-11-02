"""Scientific invariants, deliberately independent of the implementation formulae."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import math
import unittest
import numpy as np

from qc.analysis import C, Options, analyze, combination, center_arcs
from qc.native import Engine
from qc.rinex import RinexError, read_observation

ROOT=Path(__file__).resolve().parents[1]
INPUT=(ROOT/"samples") if (ROOT/"samples"/"Z061_61028.nav").exists() else (ROOT.parent/"rnx-61-66-1028")
NAV=INPUT/"Z061_61028.nav"
F1,F2=1575.42e6,1227.60e6


def measurements(rho=23000000,iono=8,n1=50,n2=30,noise1=0,noise2=0):
    # Independently generate physical code and phase observations.
    iono2=iono*(F1/F2)**2
    return ([rho+iono+noise1,(rho-iono)/(C/F1)+n1,40,None,0],
            [rho+iono2+noise2,(rho-iono2)/(C/F2)+n2,38,None,0])


def fixture(path,slip=True,missing=False):
    def h(text,label): return f"{text:<60}{label}\n"
    text=h('     3.04           OBSERVATION DATA    M: Mixed','RINEX VERSION / TYPE')
    text+=h('  1172694.9824  4899876.2970  3900680.5025','APPROX POSITION XYZ')
    text+=h('G    6 C1C L1C S1C C2W L2W S2W','SYS / # / OBS TYPES')
    text+=h('    30.000','INTERVAL')+h('','END OF HEADER')
    for i in range(22):
        if missing and i==15: continue
        v1,v2=measurements(23000000+i*10,8+i*.001,50+(1 if slip and i>=8 else 0),30)
        if i==12:v1[4]=1
        if missing and i==16:v1[1]=None;v1[4]=1
        text+=f'> 2026 10 28 08 {i//2:02d} {30*(i%2):10.7f}  0  1\n'
        fields=[]
        for value,lli in [(v1[0],0),(v1[1],v1[4]),(40,0),(v2[0],0),(v2[1],0),(38,0)]:
            fields.append((f'{value:14.3f}' if value is not None else ' '*14)+(str(lli) if lli else ' ')+' ')
        text+='G04'+''.join(fields)+'\n'
    path.write_text(text,encoding='ascii')


class CombinationTests(unittest.TestCase):
    def test_ionosphere_and_geometry_cancel_in_mw_and_mp(self):
        result=[combination(*measurements(rho=2.3e7+2000*i,iono=i),F1,F2) for i in range(10)]
        for column in (1,2,3):
            self.assertLess(np.ptp([r[column] for r in result]),1e-6)
        self.assertAlmostEqual(result[0][1],(50-30)*C/(F1-F2),places=6)

    def test_one_cycle_slip_has_expected_wavelength(self):
        a=combination(*measurements(n1=50),F1,F2)
        b=combination(*measurements(n1=51),F1,F2)
        self.assertAlmostEqual(b[0]-a[0],C/F1,places=6)
        self.assertAlmostEqual(b[1]-a[1],C/(F1-F2),places=6)

    def test_pseudorange_noise_appears_in_corresponding_mp(self):
        a=combination(*measurements(),F1,F2)
        b=combination(*measurements(noise1=2,noise2=-3),F1,F2)
        self.assertAlmostEqual(b[2]-a[2],2,places=7)
        self.assertAlmostEqual(b[3]-a[3],-3,places=7)
        self.assertEqual(a[0],b[0])

    def test_missing_code_does_not_hide_geometry_free(self):
        a,b=measurements();a[0]=None
        result=combination(a,b,F1,F2)
        self.assertIsNotNone(result[0]);self.assertIsNone(result[1]);self.assertIsNone(result[2]);self.assertIsNotNone(result[3])

    def test_arc_centering_excludes_short_arcs(self):
        rows=[[i,0,0,0,0,v,v,0,arc] for i,v,arc in [(0,100,1),(1,102,1),(2,104,1),(3,200,2),(4,300,3),(5,302,3),(6,304,3)]]
        rejected=center_arcs(rows,3)
        self.assertEqual(rejected,2)
        self.assertEqual([r[5] for r in rows],[-2,0,2,None,-2,0,2])


@unittest.skipUnless(NAV.exists(),'Local sample NAV required')
class NativeTests(unittest.TestCase):
    def setUp(self):self.engine=Engine(NAV)
    def tearDown(self):self.engine.close()

    def test_dop_matches_independent_design_matrix(self):
        azel=np.deg2rad([[0,65],[80,25],[165,45],[240,20],[310,40],[150,15]])
        az,el=azel.T
        H=np.array([np.cos(el)*np.sin(az),np.cos(el)*np.cos(az),np.sin(el),np.ones(len(el))]).T
        Q=np.linalg.inv(H.T@H)
        expected=[np.sqrt(np.trace(Q)),np.sqrt(np.trace(Q[:3,:3])),np.sqrt(Q[0,0]+Q[1,1]),np.sqrt(Q[2,2])]
        np.testing.assert_allclose(self.engine.dop(azel,10),expected,rtol=1e-12)

    def test_insufficient_or_singular_geometry_is_missing(self):
        self.assertEqual(self.engine.dop([[0,.5]]*5,0),[None]*4)
        self.assertEqual(self.engine.dop([[0,.5],[1,.4],[2,.7]],0),[None]*4)

    def test_glonass_channel_frequency_and_time_systems(self):
        self.assertEqual(self.engine.frequency('R01','1C',{'R01':1}),1602e6+562500)
        self.assertEqual(self.engine.frequency('C06','2I',{}),1561.098e6)
        self.assertEqual(self.engine.gpst(1793174400,'UTC')-1793174400,18)
        self.assertEqual(self.engine.gpst(1793174400,'BDT')-1793174400,14)

    def test_broadcast_positions_are_physical(self):
        obs=read_observation(INPUT/'Z061_20261028.26o')
        first=obs.epochs[0];sats=list(first.satellites)
        rows=self.engine.geometry(first.time,sats,obs.header['position'])
        usable=rows[rows[:,5]==0]
        self.assertGreater(len(usable),4)
        radii=np.linalg.norm(usable[:,2:5],axis=1)
        self.assertTrue(np.all((radii>2e7)&(radii<4.5e7)))

    def test_real_single_point_fallback(self):
        obs=read_observation(INPUT/'Z061_20261028.26o')
        pos=next((p for e in obs.epochs[:10] if (p:=self.engine.spp(e)) is not None),None)
        self.assertIsNotNone(pos)
        self.assertLess(np.linalg.norm(np.array(pos)-obs.header['position']),100)


@unittest.skipUnless(NAV.exists(),'Local sample NAV required')
class EndToEndTests(unittest.TestCase):
    def test_injected_slip_is_detected_and_deduplicated(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'test.obs';fixture(path)
            result,pairs=analyze(path,NAV,Options(mw_threshold=.3,min_arc=3))
        event=next(e for e in result['events'] if e['epoch']==8)
        self.assertEqual(set(event['detectors']),{'GF','MW'})
        self.assertEqual(len([e for e in result['events'] if e['epoch']==8]),1)
        self.assertEqual(result['summary']['events'],2)
        for pair in pairs.values():
            self.assertLess(pair['mp1']['rms'],.002)  # RINEX 0.001-cycle quantization

    def test_gap_and_empty_phase_lli_are_separate(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'test.obs';fixture(path,slip=False,missing=True)
            result,pairs=analyze(path,NAV,Options(min_arc=3))
        self.assertEqual(result['summary']['lli_blank_fields'],1)
        self.assertEqual(result['summary']['lli_phase_fields'],1)
        self.assertEqual(result['summary']['gaps'][0]['missing_epochs'],1)
        self.assertEqual(result['summary']['events'],1)
        rows=next(iter(pairs.values()))['rows']
        resumed=next(r for r in rows if r[0]==16)
        self.assertIsNone(resumed[2]);self.assertIsNone(resumed[4])

    def test_reader_rejects_truncated_and_duplicate_epochs(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'test.obs';fixture(path)
            text=path.read_text();lines=text.splitlines()
            path.write_text('\n'.join(lines[:-1])+'\n')
            with self.assertRaises(RinexError):read_observation(path)
            path.write_text(text+'\n'.join(lines[-2:])+'\n')
            with self.assertRaises(RinexError):read_observation(path)

    def test_bad_options_are_rejected(self):
        for kwargs in [{'gf_threshold':float('nan')},{'systems':''},{'min_arc':1},{'position':[0,0,0]}]:
            with self.assertRaises(ValueError):Options(**kwargs).validate()


if __name__=='__main__':unittest.main(verbosity=2)
