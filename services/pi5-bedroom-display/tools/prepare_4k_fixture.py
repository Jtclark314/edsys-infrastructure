#!/usr/bin/env python3
"""Generate and validate one private synthetic 4K24 SDR baseline, never play it.

Local CPU ffmpeg/ffprobe only: no network, Pi calls or library registration.
This pattern is not a real high-bitrate movie or HDR/60 fps qualification.
The fixed inputs/parameters are reproducible with the recorded tool versions;
cross-version byte-identical encoder output is not promised.
"""
import argparse
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

NAME='synthetic-4k24-hevc-main-sdr-30s.mkv'
FONT=Path('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf')
FRAMES=720
X265='pools=6:frame-threads=1:lookahead-threads=1:wpp=1:repeat-headers=1:colorprim=bt709:transfer=bt709:colormatrix=bt709:range=limited'
LIMITATION='Synthetic 4K24 SDR decode baseline only; not real high-bitrate movie, HDR, HDMI or playback acceptance.'


def encode_args(destination):
    pattern=(
        'testsrc2=size=3840x2160:rate=24:duration=30,'
        'noise=alls=4:allf=t+u:all_seed=20261016,'
        'drawgrid=w=120:h=120:t=1:c=white@0.25,'
        f"drawtext=fontfile={FONT}:text='SYNTHETIC SDR HEVC BASELINE  3840x2160  24 fps':"
        'fontcolor=white:fontsize=64:box=1:boxcolor=black@0.85:boxborderw=16:x=80:y=80,'
        f"drawtext=fontfile={FONT}:text='FRAME %{{n}}    TIME %{{pts\\:hms}}':"
        'fontcolor=white:fontsize=72:box=1:boxcolor=black@0.85:boxborderw=16:x=80:y=h-160,'
        'scale=in_color_matrix=bt601:out_color_matrix=bt709:in_range=tv:out_range=tv,format=yuv420p'
    )
    tone='aevalsrc=0.01*sin(2*PI*440*t)|0.01*sin(2*PI*660*t):s=48000:d=30'
    return ['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-n',
            '-filter_threads','1','-filter_complex_threads','1','-fflags','+bitexact',
            '-f','lavfi','-i',pattern,'-f','lavfi','-i',tone,
            '-map','0:v:0','-map','1:a:0','-map_metadata','-1','-map_chapters','-1',
            '-c:v','libx265','-preset','fast','-crf','24','-threads:v','8',
            '-x265-params',X265,'-pix_fmt','yuv420p','-frames:v',str(FRAMES),
            '-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-color_range','tv',
            '-c:a','aac','-b:a','128k','-ar','48000','-ac','2','-threads:a','1',
            '-af','afade=t=in:st=0:d=0.5,afade=t=out:st=29.5:d=0.5',
            '-flags:v','+bitexact','-flags:a','+bitexact','-t','30',str(destination)]


def decode_args(source,frame_dir):
    # Both outputs share one software decode: null output consumes every video
    # and audio frame; the other writes only three synthetic review PNGs.
    graph="[0:v:0]split[decoded][sample];[sample]select='eq(n,0)+eq(n,360)+eq(n,719)'[stills]"
    return ['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-n',
            '-progress','pipe:1','-nostats','-protocol_whitelist','file,pipe',
            '-hwaccel','none','-threads:v','8','-i',str(source),
            '-filter_complex_threads','1','-filter_complex',graph,
            '-map','[decoded]','-map','0:a:0','-c:v','wrapped_avframe','-c:a','pcm_s16le',
            '-f','null',os.devnull,'-map','[stills]','-c:v','png','-threads:v','1',
            '-fps_mode','vfr',str(frame_dir/'frame-%03d.png')]


def validate_probe(probe,decode_progress):
    streams=probe.get('streams',[])
    if len(streams)!=2:raise ValueError('Fixture must contain exactly video and audio')
    video=next((x for x in streams if x.get('codec_type')=='video'),{})
    audio=next((x for x in streams if x.get('codec_type')=='audio'),{})
    expected={'codec_name':'hevc','profile':'Main','width':3840,'height':2160,
              'pix_fmt':'yuv420p','color_range':'tv','color_space':'bt709',
              'color_transfer':'bt709','color_primaries':'bt709'}
    if any(video.get(k)!=v for k,v in expected.items()):raise ValueError('Video baseline does not match')
    if Fraction(video.get('avg_frame_rate','0'))!=24:raise ValueError('Expected 24 fps')
    if (audio.get('codec_name'),audio.get('sample_rate'),audio.get('channels'),audio.get('channel_layout'))!=('aac','48000',2,'stereo'):
        raise ValueError('Audio baseline does not match')
    duration=float(probe.get('format',{}).get('duration','nan'))
    if not math.isfinite(duration) or abs(duration-30)>.1:raise ValueError('Fixture duration does not match')
    fields=dict(line.split('=',1) for line in decode_progress.splitlines() if '=' in line)
    if fields.get('progress')!='end' or int(fields.get('frame','0').strip())!=FRAMES:
        raise ValueError('Full software decode did not complete all 720 frames')
    return video,audio,duration


def cpu_set():
    cores=sorted(os.sched_getaffinity(0))[:8]
    if not cores:raise RuntimeError('CPU affinity is unavailable')
    return cores


def local_run(args,cores,timeout=1800):
    # Fixed nice and idle-I/O wrappers. Affinity caps concurrent logical CPU
    # consumption even where ffmpeg creates additional control threads.
    command=['nice','-n','10','ionice','-c','3',*args]
    return subprocess.run(command,check=True,capture_output=True,text=True,
                          timeout=timeout,preexec_fn=lambda:os.sched_setaffinity(0,cores))


def file_record(path):
    digest=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):digest.update(block)
    return {'bytes':path.stat().st_size,'sha256':digest.hexdigest()}


def prepare(output):
    os.umask(0o077)
    if output.is_symlink():raise ValueError('Output directory must not be a symlink')
    output=output.resolve()
    output.mkdir(mode=0o700,parents=True,exist_ok=True)
    if output.stat().st_uid!=os.getuid():raise ValueError('Output directory must be owned by this user')
    os.chmod(output,0o700)
    targets=[output/NAME,output/'manifest.json',output/'frames']
    if any(p.exists() or p.is_symlink() for p in targets):raise FileExistsError('Existing fixture output is never replaced')
    if not FONT.is_file():raise FileNotFoundError('Reviewed local counter font is missing')
    for name in ['ffmpeg','ffprobe','nice','ionice']:
        if not shutil.which(name):raise FileNotFoundError('Required local tool is missing: '+name)
    cores=cpu_set()
    with tempfile.TemporaryDirectory(prefix='.build-',dir=output) as directory:
        stage=Path(directory);media=stage/NAME;frames=stage/'frames';frames.mkdir(mode=0o700)
        print('Generating fixed synthetic fixture locally; no playback or library changes.',flush=True)
        local_run(encode_args(media),cores)
        print('Qualifying full software decode and three synthetic frames locally.',flush=True)
        decoded=local_run(decode_args(media,frames),cores)
        probed=local_run(['ffprobe','-v','error','-protocol_whitelist','file,pipe',
                          '-show_streams','-show_format','-of','json',str(media)],cores,timeout=60)
        probe=json.loads(probed.stdout);video,audio,duration=validate_probe(probe,decoded.stdout)
        pngs=sorted(frames.glob('*.png'))
        if len(pngs)!=3:raise ValueError('Expected three synthetic review frames')
        for p in pngs:
            raw=p.read_bytes()
            if raw[:8]!=b'\x89PNG\r\n\x1a\n' or raw[16:24]!=(3840).to_bytes(4,'big')+(2160).to_bytes(4,'big'):
                raise ValueError('Invalid synthetic frame dimensions')
        manifest={'schema_version':1,'fixture':NAME,'limitation':LIMITATION,
                  'target':{'seconds':30,'frames':FRAMES,'width':3840,'height':2160,'fps':24,
                            'video':'HEVC Main 8-bit SDR BT.709 limited','audio':'AAC 48 kHz stereo',
                            'tone_peak_dbfs_before_aac':-40,'tone_frequencies_hz':[440,660],'fade_seconds':.5},
                  'resources':{'cpu_affinity':cores,'max_concurrent_logical_cpus':len(cores),'nice_increment':10,
                               'io_class':'idle','x265_params':X265,'preset':'fast','crf':24},
                  'tools':{name:local_run([name,'-version'],cores,60).stdout.splitlines()[0] for name in ['ffmpeg','ffprobe']},
                  'video_stream':video,'audio_stream':audio,'container_seconds':duration,
                  'software_decode':{'completed':True,'decoded_frames':FRAMES,'audio_decoded':True,'hardware_acceleration':'none'},
                  'files':{NAME:file_record(media),**{'frames/'+p.name:file_record(p) for p in pngs}},
                  'sample_frame_numbers':[0,360,719],
                  'source_sha256':file_record(Path(__file__))['sha256']}
        # Do not retain ffprobe's private temporary filename or arbitrary tags.
        manifest['ffprobe_format']={k:probe['format'][k] for k in ['format_name','duration','size','bit_rate'] if k in probe['format']}
        (stage/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        for name in [NAME,'frames','manifest.json']:(stage/name).rename(output/name)
    print(json.dumps({'fixture':str(output/NAME),'manifest':str(output/'manifest.json'),
                      'sha256':manifest['files'][NAME]['sha256'],'decode_qualified':True,'playback_tested':False}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    prepare(args.output_dir)
