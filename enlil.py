#!/usr/bin/env python3
#
# BSD 3-Clause License
#
# Copyright (c) 2022-2023 Fred W6BSD
# All rights reserved.
#
#

import json
import logging
import os
import shutil
import sys
import time

from subprocess import Popen, PIPE
from urllib.request import urlretrieve

import yaml

from PIL import Image

# https://services.swpc.noaa.gov/products/animations/enlil.json
CONFIG_NAME = 'enlil.yaml'
NOAA = "https://services.swpc.noaa.gov"
SOURCE_JSON = NOAA + "/products/animations/enlil.json"

logging.basicConfig(format='%(asctime)s %(name)s:%(lineno)d %(levelname)s - %(message)s',
                    datefmt='%Y/%m/%d %H:%M:%S', level=logging.INFO)
logger = logging.getLogger('enlil')

def read_config():
  home = os.path.expanduser('~')
  config_path = (
    os.path.join('.', CONFIG_NAME),
    os.path.join(home, '.' + CONFIG_NAME),
    os.path.join(home, '.local', CONFIG_NAME),
    os.path.join('/etc', CONFIG_NAME),
  )
  for filename in config_path:
    if os.path.exists(filename):
      break
    logger.debug('Config file "%s" not found', filename)
  else:
    logger.error('No Configuration file found')
    sys.exit(os.EX_CONFIG)

  logger.debug('Reading config file "%s"', filename)
  with open(filename, 'r', encoding='utf-8') as confd:
    config = yaml.safe_load(confd)
  return type('Config', (object,), config)


def add_margin(im_name, top, right, bottom, left, color=(0x28, 0x28, 0x28)):
  image = Image.open(im_name)
  width, height = image.size
  new_width = width + right + left
  new_height = height + top + bottom
  new_image = Image.new(image.mode, (new_width, new_height), color)
  new_image.paste(image, (left, top))
  new_image.save(im_name, quality=95)

def retrieve_files(enlil_file, target_dir):
  try:
    file_time = os.stat(enlil_file).st_mtime
    if time.time() - file_time > 3600:
      raise FileNotFoundError
  except FileNotFoundError:
    urlretrieve(SOURCE_JSON, enlil_file)
    logger.info('Downloading: %s, into: %s', SOURCE_JSON, enlil_file)

  with open(enlil_file, 'r', encoding='utf-8') as fdin:
    data_source = json.load(fdin)
    new_cnt = 0
    for url in data_source:
      filename = os.path.basename(url['url'])
      target_name = os.path.join(target_dir, filename)
      if os.path.exists(target_name):
        continue
      urlretrieve(NOAA + url['url'], target_name)
      add_margin(target_name, 0, 0, 50, 0)
      logger.info('%s saved', target_name)
      new_cnt += 1
  logger.info('%d new files downloaded', new_cnt)
  return new_cnt

def purge(enlil_file, target_dir):
  """Cleanup old enlil image that are not present in the json manifest"""
  logger.info('Cleaning up non active Enlil images')
  current_files = set([])
  with open(enlil_file, 'r', encoding='utf-8') as fdm:
    data = json.load(fdm)
    for entry in data:
      current_files.add(os.path.basename(entry['url']))

  names = (n for n in os.listdir(target_dir) if n.startswith('enlil_com'))
  count = 0
  for name in names:
    if name not in current_files:
      try:
        os.unlink(os.path.join(target_dir, name))
        logger.debug('Delete file: %s', name)
        count += 1
      except IOError as exp:
        logger.error(exp)
  logger.info('%d files deleted', count)

def select_files(source_dir):
  file_list = []
  for name in os.listdir(source_dir):
    if not name.startswith('enlil_com') and not name.endswith('.jpg'):
      continue
    file_list.append(name)
  logger.info('%d files selected for animation', len(file_list))
  return sorted(file_list)


def create_links(source_dir, target_dir, file_list):
  logger.info('Creating workspace %s', target_dir)
  if not file_list:
    return
  for idx, name in enumerate(file_list):
    target = os.path.join(target_dir, f"enlil-{idx:05d}.jpg")
    source = os.path.join(source_dir, name)
    os.link(source, target)
    logger.debug('Target file: %s', target)


def mk_video(src, video_file):
  ffmpeg = shutil.which('ffmpeg')
  logfile = '/tmp/enlil_animation.log'
  tmp_file = f"{video_file}-{os.getpid()}.mp4"
  input_files = os.path.join(src, 'enlil-%05d.jpg')
  in_args = f'-y -framerate 10 -i {input_files}'.split()
  ou_args = '-an -c:v libx264 -pix_fmt yuv420p -vf scale=700:474'.split()
  cmd = [ffmpeg, *in_args, *ou_args, tmp_file]
  logger.info('Writing ffmpeg output in %s', logfile)
  logger.info("Saving %s video file", tmp_file)
  with open(logfile, "a", encoding='ascii') as err:
    err.write(' '.join(cmd))
    err.write('\n\n')
    err.flush()
    with Popen(cmd, shell=False, stdout=PIPE, stderr=err) as proc:
      proc.wait()
    if proc.returncode != 0:
      logger.error('Error generating the video file')
      return
    logger.info('mv %s %s', tmp_file, video_file)
    os.rename(tmp_file, video_file)

def cleanup(directory):
  for name in os.listdir(directory):
    os.unlink(os.path.join(directory, name))
  os.rmdir(directory)
  logger.info('Working directory "%s" removed', directory)


def animate(source_dir, video_file):
  pid = os.getpid()
  try:
    work_dir = os.path.join(source_dir, f"workdir-{pid}")
    os.mkdir(work_dir)
    files = select_files(source_dir)
    create_links(source_dir, work_dir, files)
    mk_video(work_dir, video_file)
  except KeyboardInterrupt:
    logger.warning("^C pressed")
    sys.exit(os.EX_SOFTWARE)
  finally:
    cleanup(work_dir)

def main():
  logger.setLevel(logging.getLevelName(os.getenv('LOG_LEVEL', 'INFO')))
  config = read_config()

  if not os.path.isdir(config.target_dir):
    logger.warning("The target directory %s does not exist", config.target_dir)
    try:
      os.makedirs(config.target_dir)
    except IOError as err:
      logger.error(err)
      sys.exit(os.EX_IOERR)

  video_file = os.path.join(config.video_dir, config.video_file)
  if retrieve_files(config.enlil_file, config.target_dir) or not os.path.exists(video_file):
    purge(config.enlil_file, config.target_dir)
    animate(config.target_dir, video_file)
  else:
    logger.info('No new data')

if __name__ == "__main__":
  main()
