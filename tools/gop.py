#!/usr/bin/env python

# Copyright Google Inc. Apache 2.0.

import argparse
import datetime
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy
import os.path
import pandas as pd
import pts_utils
import re
import subprocess
import sys

M2PB = 'm2pb'

## axes.formatter.useoffset not in 1.3.1
#mpl.rcParams['axes.formatter.useoffset'] = False

DEFAULT_PACKET_LENGTH = 10000
# marker size for non-pusi packets
NON_PUSI_MARKERSIZE = 3


def get_opts(argv):
  # init parser
  parser = argparse.ArgumentParser(description='Parse mpeg-ts file.')
  # usage = 'usage: %prog [options] arg1 arg2'
  # parser = argparse.OptionParser(usage=usage)
  # parser.print_help() to get argparse.usage (large help)
  # parser.print_usage() to get argparse.usage (just usage line)
  parser.add_argument('-d', '--debug', dest='debug', default=0,
      action='count',
      help='Increase verbosity (specify multiple times for more)')
  parser.add_argument('--quiet', action='store_const',
      dest='debug', const=-1,
      help='Zero verbosity',)
  parser.add_argument('--xmin', action='store',
      dest='xmin', type=long, default=pts_utils.kPtsInvalid,
      metavar='X_AXIS_MIN',
      help='use xmin for the x axis',)
  parser.add_argument('--xmax', action='store',
      dest='xmax', type=long, default=pts_utils.kPtsInvalid,
      metavar='X_AXIS_MAX',
      help='use xmax for the x axis',)
  parser.add_argument('--ymin', action='store',
      dest='ymin', type=long, default=pts_utils.kPtsInvalid,
      metavar='Y_AXIS_MIN',
      help='use ymin for the y axis',)
  parser.add_argument('--ymax', action='store',
      dest='ymax', type=long, default=pts_utils.kPtsInvalid,
      metavar='Y_AXIS_MAX',
      help='use ymax for the y axis',)
  parser.add_argument('--video-pid', action='store',
      dest='videostr_pid', type=long,
      metavar='VIDEO PID',
      help='specify video pid',)
  parser.add_argument('--audio-pid', action='append',
      dest='audiostr_pid_l', default=[], type=int,
      metavar='AUDIO PID',
      help='specify audio pid',)
  parser.add_argument('--delta', nargs = '*', action='append',
      dest='delta',
      help='Add pts delta mapping',)
  parser.add_argument('--pusi-skip', action='store_const',
      dest='pusi_skip', default=False, const=True,
      help='Skip samples without pusi',)
  parser.add_argument('-v', '--version', action='version',
      version='%(prog)s 1.0')
  # add sub-parsers
  subparsers = parser.add_subparsers()
  # independent sub-commands
  parser_pts = subparsers.add_parser('pts', help='show stream pts graph')
  parser_pts.set_defaults(subcommand='pts')
  parser_summary = subparsers.add_parser('summary', help='summary')
  parser_summary.set_defaults(subcommand='summary')
  # do the parsing
  for p in (parser, parser_pts, parser_summary):
    p.add_argument('-o', '--output', action='store',
        dest='output_filename',
        metavar='OUTPUT_FILENAME',
        help='output filename',)
  for p in (parser_pts, parser_summary):
    p.add_argument('input_file', nargs=1, help='input log file')
    p.add_argument('remaining', nargs=argparse.REMAINDER)
  return parser.parse_args(argv[1:])


videostr_pid = 481
audiostr_pid_d = { 482: 1, 483: 2 }
pmtstr = ' program_map_section {'

video_stream_type_l = [
    0x1b,  # H.264/14496-10 video (MPEG-4/AVC)
]

audio_stream_type_l = [
    0x0f,  # 13818-7 Audio with ADTS transport syntax
    0x81,  # User private (commonly Dolby/AC-3 in ATSC)
]


headerre = r"""
    packet:\s(?P<packet>[0-9]+)
    \s
    byte:\s(?P<byte>[0-9]+)
    \s
    parsed\s{
    \s
    header\s{
      [^}]*
      \s
      payload_unit_start_indicator:\s(?P<pusi>(true|false))
      \s
      [^}]*
      \s
      pid:\s(?P<pid>[0-9]+)
      \s
      [^}]*
      \s
    }
    (?P<rem>.*)
"""


# PAT
# packet: 0 byte: 0 parsed { header { transport_error_indicator: false payload_unit_start_indicator: true transport_priority: false pid: 0 transport_scrambling_control: 0 adaptation_field_exists: false payload_exists: true continuity_counter: 0 } psi_packet { pointer_field: "" program_association_section { table_id: 0 transport_stream_id: 1 version_number: 0 current_next_indicator: true section_number: 0 last_section_number: 0 program_information { program_number: 1 program_map_pid: 480 } crc_32: 760248324 } } }

# PMT
# packet: 1 byte: 188 parsed { header { transport_error_indicator: false payload_unit_start_indicator: true transport_priority: false pid: 480 transport_scrambling_control: 0 adaptation_field_exists: false payload_exists: true continuity_counter: 0 } psi_packet { pointer_field: "" program_map_section { table_id: 2 program_number: 1 version_number: 0 current_next_indicator: true section_number: 0 last_section_number: 0 pcr_pid: 481 mpegts_descriptor { tag: 14 length: 3 data: "\300<x" } stream_description { stream_type: 27 elementary_pid: 481 mpegts_descriptor { tag: 40 length: 4 data: "M@(?" } mpegts_descriptor { tag: 14 length: 3 data: "\300:\230" } } stream_description { stream_type: 129 elementary_pid: 482 mpegts_descriptor { tag: 5 length: 4 data: "AC-3" } mpegts_descriptor { tag: 129 length: 7 data: "\006(\005\377\037\001?" } mpegts_descriptor { tag: 10 length: 4 data: "und\000" } mpegts_descriptor { tag: 14 length: 3 data: "\300\001\340" } } crc_32: 1966564032 } } }
pmtre = r"""
    parsed
    .*?
    header\s{
      \s
      .*?
      \s
      pid:\s(?P<pid>[0-9]+)
      \s
      .*?
      \s
      program_map_section\s{
      .*?
    (?P<rem>stream_description\s{.*)
"""

pmtstreamre = r"""
    stream_description\s{
      \s
      stream_type:\s(?P<stream_type>[0-9]+)
      \s
      elementary_pid:\s(?P<elementary_pid>[0-9]+)
      \s
      [^}]*
    }
    (?P<rem>.*)
"""


# video
# packet: 2 byte: 376 parsed { header { transport_error_indicator: false payload_unit_start_indicator: true transport_priority: false pid: 481 transport_scrambling_control: 0 adaptation_field_exists: true payload_exists: true continuity_counter: 1 } adaptation_field { adaptation_field_length: 7 discontinuity_indicator: true random_access_indicator: true elementary_stream_priority_indicator: true splicing_point_flag: false transport_private_data_flag: false pcr { base: 18039 extension: 23 } } pes_packet { stream_id: 224 stream_id_type: STREAM_ID_VIDEO_13818 pes_packet_length: 0 pes_scrambling_control: 0 pes_priority: 0 data_alignment_indicator: true copyright: false original_or_copy: false pes_header_data_length: 10 pts: 183003 dts: 180000 pes_packet_data_byte: "\000\000\000\001\t\020\000\000\000\001\'M@(\344`<\002#\357\001\020\000\000>\220\000\016\246\016(\000\002\334l\000\005\270\336\367\270\017\204B)\300\000\000\000\001(\372K\310\000\000\000\001\006\000\007\201<h\000#(@\200\000\000\000\001\006\001\007\000\000\003\000\000\003\000\0022\200\000\000\000\001\006\005H\217\273lt|>Ox\237\007\214\263]<\027~Elemental Video Engine(tm) www.elementaltechnolo" } }
# packet: 3 byte: 564 parsed { header { transport_error_indicator: false payload_unit_start_indicator: false transport_priority: false pid: 481 transport_scrambling_control: 0 adaptation_field_exists: false payload_exists: true continuity_counter: 2 } data_bytes: "gies.com\200\000\000\000\001%\210\201\000@-_\376\367\261\217\201M\307Y\r\2279T\234j\327\222\335\003\272\331\350\'\365\036\237\365\026IJikJB\323\303\220\353\252\216m \003\373\035\242\315\322\213/\254\202r\220\232M&\223I\244\322i4\232NM\007\275\230\355\207\311\354\\\261%\327:>za\334\202\027\325u\372\216\361\357;\366\230\026\237\317\347\363\371\374\376\177?\237\317\351Q\006\212\305b\261X\254V+\025\212\305b\261X\254V+\025\212\305b\261X\254V+\025\212\305b\261YU-E\275\026\364[\321oE\275\026\364[\321oE\275\026\364[\321oE\275" }

videore = r"""
    .*?
    pes_packet\s{
      [^}]*
      \spts:\s(?P<pts>[0-9]+)
"""

# audio
# packet: 8116 byte: 1525808 parsed { header { transport_error_indicator: false payload_unit_start_indicator: true transport_priority: false pid: 482 transport_scrambling_control: 0 adaptation_field_exists: false payload_exists: true continuity_counter: 1 } pes_packet { stream_id: 189 stream_id_type: STREAM_ID_PRIVATE_STREAM_1 pes_packet_length: 1544 pes_scrambling_control: 0 pes_priority: 0 data_alignment_indicator: true copyright: false original_or_copy: false pes_header_data_length: 5 pts: 183003 pes_packet_data_byte: "\013w\034\014\0240C\037\3677$\222\000\000\336l[\204\004\004\004\020\200\200\200\202\n\343\347\317\237>|\371\363\347\317\237>|\371\363\347\317\237>|\371\363\347\317\237>|\371\363\347\317\237\177\316\257\237>|\371\363\347\317\237>|\371\363\347\317\237>|\371\363\347\317\237>|\371\363\347\317\237\177\363\253\347\317\237>|\371\363\347\317\237>|\371\363\347\317\237>|\371\363\347\317\237>|\371\363\347\336S\342H\221$\000\000\000\000\003\306\333m\266\333\307\217\036;\273\273\270\000\000\000\000\000\000\000\000\000\000\000\356\356\356\356\356\356\333m\266\333o\2375" } }
# packet: 8148 byte: 1531824 parsed { header { transport_error_indicator: false payload_unit_start_indicator: false transport_priority: false pid: 482 transport_scrambling_control: 0 adaptation_field_exists: false payload_exists: true continuity_counter: 2 } data_bytes: "\255kZ\326\265\255kZ\326\265\255kZ\326\265\240\000\000\000\033m\266\333o\036<x\356\356\356\340\000\000\000\000\000\000\000\000\000\000\003\273\273\273\273\273\273m\266\333m\276|\326\265\255kZ\326\230\000\000\000\000\000\007\215\266\333m\267\217\036<wwwp\000\000\000\000\000\000\000\000\000\000\001\335\335\335\335\335\335\266\333m\266\337>kZ\326\265\255kZ\326\265\255kZ\326\265\255k@\000\000\0006\333m\266\336<x\361\335\335\335\300\000\000\000\000\000\000\000\000\000\000\007wwwwwv\333m\266\333|\371\255kZ\326\265\2550\000\000\000\000\000\017\033m\266\333o\036<x\356\356\356\340\000\000" }

audiore = r"""
    .*?
    pes_packet\s{
      [^}]*
      \spes_packet_length:\s(?P<pes_packet_length>[0-9]+)
      [^}]*
      \spts:\s(?P<pts>[0-9]+)
"""

scte35re = 'pid: 0x01ea pusi:'






# scte35
# == byte: 2396328088 packet: 12746427 ts_header { pid: 0x01ea pusi: 1 stream_type: 0x86 stream_type_str: "User private" } pes_header { start_code: 00fc30 stream_id: 0x20 stream_id_str: "Slice, vertical posn 32 " pes_packet_length: 0 type: "MPEG-1 packet layer packet" } data { len: 165 bytes: 7fcffececfe61c00000000000048bc26b5ffffff... }

# other
# == byte: 448634364 packet: 2386354 ts_header { pid: 0x1ffe pusi: 1 stream_type: 0x06 stream_type_str: "H.222.0/13818-1 PES private data (maybe Dolby/AC-3 in DVB)" } pes_header { start_code: 000001 stream_id: 0xf0 stream_id_str: "SYSTEM START: ECM stream " pes_packet_length: 176 type: "MPEG-1 packet layer packet"error: "MPEG-1 PES packet has 0x0X instead of 0x40, 0x2X, 0x3X or 0x0F" } data { len: 165 bytes: 8900000001000027100200017700e86d1486083d... }

unknown_stream_type_l = []

def parse_pmt(l):
  global videostr_pid
  global audiostr_pid_d
  # remove the header
  pmt_match = re.search(pmtre, l, re.X)
  if not pmt_match:
    return
  # parse the stream_description fields
  rem = pmt_match.group('rem')
  pmt = {}
  pmt_match = re.search(pmtstreamre, rem, re.X)
  while pmt_match:
    elementary_pid = int(pmt_match.group('elementary_pid'))
    stream_type = int(pmt_match.group('stream_type'))
    rem = pmt_match.group('rem')
    if stream_type in video_stream_type_l:
      pmt['video'] = elementary_pid
    elif stream_type in audio_stream_type_l:
      if 'audio' not in pmt:
        pmt['audio'] = []
      pmt['audio'].append(elementary_pid)
    else:  # unknown
      if stream_type not in unknown_stream_type_l:
        print 'unknown stream_type: %s' % stream_type
        unknown_stream_type_l.append(stream_type)
    pmt_match = re.search(pmtstreamre, rem, re.X)
  # replace pmts
  if 'video' in pmt:
    videostr_pid = pmt['video']
  audiostr_pid_d = {}
  i = 1
  if 'audio' in pmt:
    for pid in pmt['audio']:
      audiostr_pid_d[pid] = i
      i += 1


def dump_frame_info(input_file, delta_l, debug, pusi_skip=False):
  lst = []
  command = [M2PB, '--packet', '--pts', '--pusi', '--pid', '--type',
      'dump', input_file]
  if debug > 0:
    print ' '.join(command)
  proc = subprocess.Popen(command, stdout=subprocess.PIPE)
  last_pts_d = {}
  start_pts = pts_utils.kPtsInvalid
  pts_delta = 0
  dumped_lines_d = {}
  raw_packets = 0
  for line in iter(proc.stdout.readline,''):
    l = line.rstrip()
    packet, pts, pusi, pid, t = l.split()
    packet = long(packet)
    pts = long(pts) if pts != '-' else pts_utils.kPtsInvalid
    pusi = (pusi == '1')
    try:
      pid = int(pid)
    except ValueError:
      # raw ts packet
      raw_packets += 1
    if pusi_skip and not pusi:
      continue

    if pid == videostr_pid or pid in audiostr_pid_d.keys():
      # ensure a valid type
      if t == '-' and pid == videostr_pid:
        t = 'V'
      if t == '-' and pid in audiostr_pid_d.keys():
        t = '%i' % audiostr_pid_d[pid]
      # use the packet number to clock the dump
      pts_orig = pts_utils.kPtsInvalid
      if pusi:
        pts_orig = pts
        last_pts_d[pid] = pts_orig
      elif pid in last_pts_d:
        pts_orig = last_pts_d[pid]
      # check the delta
      if len(delta_l) > 0 and delta_l[0][0] == pts_orig:
        # set the new delta
        pts_delta = delta_l[0][1]
        print '#setting pts_delta: %i' % pts_delta
        delta_l = delta_l[1:]
      pts = pts_utils.pts_add(pts_orig, pts_delta)
      if start_pts == pts_utils.kPtsInvalid:
        start_pts = pts
      if debug > 1:
        print '%i %i %i %i %s' % (packet, pts_orig, pts, pusi, t)
      if pts == pts_utils.kPtsInvalid:
        if pid not in dumped_lines_d:
          dumped_lines_d[pid] = 1
        else:
          dumped_lines_d[pid] += 1
        if debug > 2:
          print 'error: dumping %s' % line
      else:
        lst.append([packet, pts_orig, pts, pusi, t])

  for pid in dumped_lines_d:
    print 'error: dumped %i lines for pid %i' % (dumped_lines_d[pid], pid)

  if not lst:
    print 'error: no valid lines read from %r' % command
    sys.exit(-1)

  if raw_packets:
    print 'warning: found %i raw packets' % raw_packets
  return pd.DataFrame(lst, columns=['packet', 'pts_orig', 'pts', 'pusi',
      'type'])
  #return numpy.array(lst, dtype=dtype)


def dump_frame_info_inefficient(input_file, delta_l, debug, pusi_skip=False):
  lst = []
  command = [M2PB, 'totxt', input_file]
  proc = subprocess.Popen(command, stdout=subprocess.PIPE)
  last_pts_d = {}
  start_pts = pts_utils.kPtsInvalid
  pts_delta = 0
  dumped_lines_d = {}
  for line in iter(proc.stdout.readline,''):
    l = line.rstrip()
    # use simpler comparison for faster parsing
    #header_match = re.search(headerre, l, re.X)
    #if not header_match:
    #  print '#invalid line: %s' % l
    #  continue
    #packet = long(header_match.group('packet'))
    #pid = int(header_match.group('pid'))
    #pusi = header_match.group('pusi') == 'true'
    parts = l.split(' ')
    if parts[0] != 'packet:' or len(parts) < 16:
      print '#invalid line: %s' % l
      continue
    packet = long(parts[1])
    pid = int(parts[15])
    pusi = parts[11] == 'true'

    if pusi_skip and not pusi:
      continue

    if pmtstr in l:
      parse_pmt(l)

    elif pid == videostr_pid:
      # use the packet number to clock the dump
      pts_orig = pts_utils.kPtsInvalid
      if pusi:
        video_match = re.search(videore, l, re.X)
        if not video_match:
          continue
        pts_orig = long(video_match.group('pts'))
        last_pts_d[pid] = pts_orig
      elif pid in last_pts_d:
        pts_orig = last_pts_d[pid]
      # check the delta
      if len(delta_l) > 0 and delta_l[0][0] == pts_orig:
        # set the new delta
        pts_delta = delta_l[0][1]
        print '#setting pts_delta: %i' % pts_delta
        delta_l = delta_l[1:]
      pts = pts_utils.pts_add(pts_orig, pts_delta)
      #h264_type = video_match.group('h264_type')
      h264_type = 'V'
      if start_pts == pts_utils.kPtsInvalid:
        start_pts = pts
      if debug > 0:
        print '%i %i %i %i %s' % (packet, pts_orig, pts, pusi, h264_type)
      if pts == pts_utils.kPtsInvalid:
        if pid not in dumped_lines_d:
          dumped_lines_d[pid] = 1
        else:
          dumped_lines_d[pid] += 1
        if debug > 0:
          print 'error: dumping %s' % line
      else:
        lst.append([packet, pts_orig, pts, pusi, h264_type])

    elif pid in audiostr_pid_d.keys():
      # use the packet number to clock the dump
      pts_orig = pts_utils.kPtsInvalid
      if pusi:
        audio_match = re.search(audiore, l, re.X)
        if not audio_match:
          continue
        pts_orig = long(audio_match.group('pts'))
        last_pts_d[pid] = pts_orig
      elif pid in last_pts_d:
        pts_orig = last_pts_d[pid]
      # check the delta
      # ZZZ
      pts = pts_utils.pts_add(pts_orig, pts_delta)
      if debug > 0:
        print '%i %i %i %i %i' % (packet, pts_orig, pts, pusi,
            audiostr_pid_d[pid])
      if pts == pts_utils.kPtsInvalid:
        if pid not in dumped_lines_d:
          dumped_lines_d[pid] = 1
        else:
          dumped_lines_d[pid] += 1
        if debug > 0:
          print 'error: dumping %s' % line
      else:
        lst.append([packet, pts_orig, pts, pusi, '%s' % audiostr_pid_d[pid]])

  for pid in dumped_lines_d:
    print 'error: dumped %i lines for pid %i' % (dumped_lines_d[pid], pid)

  if not lst:
    print 'error: no valid lines read from %r' % command
    sys.exit(-1)

  return pd.DataFrame(lst, columns=['packet', 'pts_orig', 'pts', 'pusi',
      'type'])
  #return numpy.array(lst, dtype=dtype)
  #return numpy.array(lst)



def do_plot(df, filename, xmin, xmax, ymin, ymax):
  global audiostr_pid_d
  # ensure sensible values here
  if xmin == pts_utils.kPtsInvalid:
    xmin = min(df.packet)
  if xmax == pts_utils.kPtsInvalid:
    xmax = max(df.packet)
  # subset based on xmin:xmax
  tdf = df[(df.packet >= xmin) & (df.packet <= xmax)]
  if ymin == pts_utils.kPtsInvalid:
    ymin = min(tdf.pts)
  if ymax == pts_utils.kPtsInvalid:
    ymax = max(tdf.pts)
  # provide some presentation margins
  xmargin = (xmax - xmin) / 20.
  xmin = xmin - xmargin
  xmax = xmax + xmargin
  ymargin = (ymax - ymin) / 20.
  ymin = ymin - ymargin
  ymax = ymax + ymargin
  # subset the dataframe now
  VIDEO_TYPE_L = ('I', 'P', 'B', 'V')
  video_df = tdf[[t in VIDEO_TYPE_L for t in tdf.type]]
  AUDIO_TYPE_L = [str(i) for i in audiostr_pid_d.values()]
  audio_df = tdf[[t in AUDIO_TYPE_L for t in tdf.type]]
  plt.xlim([xmin, xmax])
  plt.ylim([ymin, ymax])
  # print the whole drawing
  plt.plot(video_df.packet, video_df.pts, '-b')
  plt.gca().set_xlabel('packet number', ha='left', va = 'top')
  plt.gca().xaxis.set_label_coords(0.9, -0.05)
  #plt.gca().set_ylabel('pts', ha='center', va = 'top')
  plt.ylabel('pts')
  plt.gca().yaxis.set_label_coords(-0.05, 1.05)
  # print the video frames
  plt.plot(video_df.packet, video_df.pts, linestyle='-', marker='+', color='b',
      markersize=NON_PUSI_MARKERSIZE)
  for ft in VIDEO_TYPE_L:
    tmp_df = video_df[(video_df.type == ft) & (video_df.pusi == True)]
    plt.scatter(tmp_df.packet, tmp_df.pts, marker = r"$\mathtt{%s}$" % ft, s=40)
  # http://stackoverflow.com/questions/22408237/named-colors-in-matplotlib
  color_d = {
      '1': 'r',
      '2': 'g',
      '3': 'c',
      '4': 'm',
      '5': 'darkred',
      '6': 'darkgreen',
      '7': 'darkcyan',
      '8': 'darkmagenta',
      '9': 'y',
  }
  for ft in AUDIO_TYPE_L:
    this_audio_df = audio_df[(audio_df.type == ft)]
    plt.plot(this_audio_df.packet, this_audio_df.pts,
        linestyle='-', marker='+', color=color_d[ft],
        markersize=NON_PUSI_MARKERSIZE)
  for ft in AUDIO_TYPE_L:
    tmp_df = audio_df[(audio_df.type == ft) & (audio_df.pusi == True)]
    plt.scatter(tmp_df.packet, tmp_df.pts, marker = r"$\mathtt{%s}$" % ft, s=40)
  # add bar
  #plt.plot((pts_bar, pts_bar), (ylim[0], ylim[1]), 'k-')
  # make axis values absolute (instead of shift-offset)
  plt.gca().get_xaxis().get_major_formatter().set_useOffset(False)
  plt.gca().get_yaxis().get_major_formatter().set_useOffset(False)
  plt.gca().get_yaxis().get_major_formatter().set_scientific(False)
  #plt.show()
  ## print a horizontal line
  #plt.axhline(y=1600000, color='k')
  ## print a vertical line
  #plt.axvline(x=74548, color='k', ls='dotted')
  plt.savefig(filename)


def dump_frame_summary(input_file, delta_l, debug):
  lst = []
  command = [M2PB, '--packet', '--byte', '--pts', '--pid', '--type',
      'dump', input_file]
  if debug > 0:
    print ' '.join(command)
  proc = subprocess.Popen(command, stdout=subprocess.PIPE)
  last_pts_d = {}
  start_pts = pts_utils.kPtsInvalid
  pts_delta = 0
  dumped_lines_d = {}
  raw_packets = 0
  # init counters
  video_pkts_ = 0
  audio_pkts_ = 0
  other_pkts_ = 0
  video_gop_cnt = -1
  video_frame_index = 0
  for line in iter(proc.stdout.readline,''):
    l = line.rstrip()
    packet, byte, pts, pid, t = l.split()
    pts = long(pts) if pts != '-' else pts_utils.kPtsInvalid
    try:
      pid = int(pid)
    except ValueError:
      # raw ts packet
      raw_packets += 1
    if t == '-':
      # just count the packet
      if pid == videostr_pid:
        video_pkts_ += 1
      elif pid in audiostr_pid_d.keys():
        audio_pkts_ += 1
      else:
        other_pkts_ += 1
      continue
    # packet with type
    if t == 'I':
      video_gop_cnt += 1
      video_frame_index = 0
    elif t in ('P', 'B', 'V'):
      video_frame_index += 1
    if pid == videostr_pid or pid in audiostr_pid_d.keys():
      print "%s, %s, %s, %s, %i, %i, %i, %i, %i" % (
          t, pts, packet, byte,
          video_gop_cnt,
          video_frame_index,
          video_pkts_, audio_pkts_, other_pkts_)
    else:
      print "ARGH"
    # init counters
    video_pkts_ = 0
    audio_pkts_ = 0
    other_pkts_ = 0


def main(argv):
  global videostr_pid
  global audiostr_pid_d
  vals = get_opts(argv)
  # check global values
  if vals.videostr_pid:
    videostr_pid = vals.videostr_pid
  if vals.audiostr_pid_l:
    audiostr_pid_d = dict((v, k+1) for (k, v) in enumerate(vals.audiostr_pid_l))
  # fix delta parsing
  new_delta = []
  if vals.delta:
    for k in vals.delta:
      new_delta.append([long(i) for i in k[0].split(',')])
  vals.delta = new_delta
  # print results
  if vals.debug > 1:
    for k, v in vars(vals).iteritems():
      print 'vals.%s = %s' % (k, v)
    print 'remaining: %r' % vals.remaining

  # get input file
  assert os.path.isfile(vals.input_file[0]), \
      'need a valid mpeg-ts input file (%s)' % vals.input_file[0]
  if vals.subcommand == 'pts':
    df = dump_frame_info(vals.input_file[0], vals.delta, vals.debug,
        vals.pusi_skip)
    if vals.output_filename:
      filename = vals.output_filename
    else:
      filename = os.path.split(vals.input_file[0])[1] + '.pdf'
    do_plot(df, filename, vals.xmin, vals.xmax, vals.ymin, vals.ymax)
    print 'written file %s' % filename
  elif vals.subcommand == 'summary':
    dump_frame_summary(vals.input_file[0], vals.delta, vals.debug)


if __name__ == '__main__':
  main(sys.argv)
