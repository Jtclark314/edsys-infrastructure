"""Reject wrong media/partial qualification without running an encoder/device."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('fixture4k',Path(__file__).parents[1]/'tools/prepare_4k_fixture.py')
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)


class FixtureQualificationTests(unittest.TestCase):
    def probe(self):
        return {'streams':[{'codec_type':'video','codec_name':'hevc','profile':'Main','width':3840,'height':2160,
                            'pix_fmt':'yuv420p','color_range':'tv','color_space':'bt709','color_transfer':'bt709',
                            'color_primaries':'bt709','avg_frame_rate':'24/1'},
                           {'codec_type':'audio','codec_name':'aac','sample_rate':'48000','channels':2,'channel_layout':'stereo'}],
                'format':{'duration':'30.021'}}

    def test_exact_baseline_and_full_software_decode_are_required(self):
        fixture.validate_probe(self.probe(),'frame=360\nprogress=continue\nframe=720\nprogress=end\n','')
        for progress in ['frame=719\nprogress=end\n','frame=720\nprogress=continue\n','']:
            with self.assertRaises(ValueError):fixture.validate_probe(self.probe(),progress,'')

    def test_concealed_decode_errors_cannot_pass_with_complete_progress(self):
        progress='frame=720\nprogress=end\n'
        for error in ['[hevc] Error parsing NAL unit\n','corrupt decoded frame\n',None]:
            with self.assertRaises(ValueError):fixture.validate_probe(self.probe(),progress,error)
        # Normal stdout progress remains valid when the error channel is empty.
        fixture.validate_probe(self.probe(),'frame=24\nout_time_us=1000000\nprogress=continue\n'+progress,'')

    def test_actual_decode_command_enables_strict_software_errors(self):
        args=fixture.decode_args(Path('/private/synthetic.mkv'),Path('/private/frames'))
        self.assertIn('-xerror',args)
        self.assertEqual(args[args.index('-err_detect')+1],'explode')
        self.assertEqual(args[args.index('-hwaccel')+1],'none')
        self.assertEqual(args[args.index('-enc_time_base:v')+1],'-1')
        self.assertEqual(args[args.index('-fps_mode')+1],'passthrough')

    def test_hdr_10bit_wrong_rate_audio_or_extra_stream_cannot_pass(self):
        for field,value in [('profile','Main 10'),('pix_fmt','yuv420p10le'),('color_transfer','smpte2084'),
                            ('width',1920),('avg_frame_rate','60/1')]:
            p=self.probe();p['streams'][0][field]=value
            with self.assertRaises(ValueError):fixture.validate_probe(p,'frame=720\nprogress=end\n','')
        for field,value in [('codec_name','flac'),('sample_rate','44100'),('channels',1)]:
            p=self.probe();p['streams'][1][field]=value
            with self.assertRaises(ValueError):fixture.validate_probe(p,'frame=720\nprogress=end\n','')
        p=self.probe();p['streams'].append({'codec_type':'subtitle'})
        with self.assertRaises(ValueError):fixture.validate_probe(p,'frame=720\nprogress=end\n','')

    def test_truncated_or_invalid_duration_cannot_pass(self):
        for duration in ['29','31','nan','inf']:
            p=deepcopy(self.probe());p['format']['duration']=duration
            with self.assertRaises(ValueError):fixture.validate_probe(p,'frame=720\nprogress=end\n','')


if __name__=='__main__':unittest.main()
