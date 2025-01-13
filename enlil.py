#!/usr/bin/env python3
#
# BSD 3-Clause License
#
# Copyright (c) 2022-2023 Fred W6BSD
# All rights reserved.
#
#

import argparse
import json
import logging
import os
import shutil
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from subprocess import PIPE, Popen
from typing import Dict, Iterator, List, Optional, Type

import yaml
from PIL import Image

# https://services.swpc.noaa.gov/products/animations/enlil.json
CONFIG_NAME = 'enlil.yaml'
NOAA = "https://services.swpc.noaa.gov"
SOURCE_JSON = NOAA + "/products/animations/enlil.json"
MARGIN_COLOR = (0x0, 0x0, 0x0)

logging.basicConfig(format='%(asctime)s %(name)s:%(lineno)d %(levelname)s - %(message)s',
                    datefmt='%Y/%m/%d %H:%M:%S', level=logging.INFO)
logger = logging.getLogger('enlil')


class Workdir:
  def __init__(self, source: Path) -> None:
    self.workdir = source.joinpath('_workdir')

  def __enter__(self) -> Path:
    try:
      self.workdir.mkdir()
      return self.workdir
    except IOError as err:
      raise err

  def __exit__(self, exc_type: Optional[Type[BaseException]],
               exc_value: Optional[BaseException],
               traceback: Optional[Type[BaseException]]) -> None:
    shutil.rmtree(self.workdir)


@dataclass(slots=True)
class Config:
  target_dir: Path
  enlil_file: Path
  video_file: Path

  def __init__(self, **kwargs: Dict[str, str]) -> None:
    # pylint: disable=no-member
    for key, val in kwargs.items():
      if key not in self.__dataclass_fields__:
        logging.warning('Configuration attribute: %s ignored', key)
        continue
      if isinstance(val, str):
        tmp_val = Path(val)
      else:
        raise TypeError(f"Unexpected type for {key}: {type(val)}")
      setattr(self, key, tmp_val)


def read_config() -> Config:
  home = Path('~').expanduser()
  config_path = (
    Path('.').joinpath(CONFIG_NAME),
    Path(home).joinpath('.' + CONFIG_NAME),
    Path(home).joinpath('.local', CONFIG_NAME),
    Path('/etc').joinpath(CONFIG_NAME),
  )
  for filename in config_path:
    if filename.exists():
      break
    logger.debug('Config file "%s" not found', filename)
  else:
    logger.error('No Configuration file found')
    sys.exit(os.EX_CONFIG)

  logger.debug('Reading config file "%s"', filename)
  with filename.open('r', encoding='utf-8') as confd:
    config = yaml.safe_load(confd)

  try:
    return Config(**config)
  except TypeError as err:
    logger.error('Configuraion error: %s', err)
    raise SystemExit(err) from None


def add_margin(im_name: Path, top: int, right: int, bottom: int, left: int) -> None:
  color = MARGIN_COLOR
  image = Image.open(im_name)
  width, height = image.size
  new_width = width + right + left
  new_height = height + top + bottom
  new_image = Image.new(image.mode, (new_width, new_height), color)
  new_image.paste(image, (left, top))
  new_image.save(im_name)


def download_with_etag(url: str, filename: Path) -> bool:
  etag_file = filename.with_suffix('.etag')
  etag = None
  if etag_file.exists():
    with open(etag_file, "r", encoding='utf-8') as fde:
      etag = fde.read().strip()

  request = urllib.request.Request(url)
  if etag:
    request.add_header("If-None-Match", etag)

  try:
    with urllib.request.urlopen(request) as response:
      if response.status == 304:
        return False
      with open(filename, "wb") as fd:
        fd.write(response.read())
      if "ETag" in response.headers:
        with open(etag_file, "w", encoding='utf-8') as fd:
          fd.write(response.headers["ETag"])
        return True
  except urllib.error.HTTPError as e:
    if e.code == 304:
      return False
    raise
  return False


def retrieve_image(source_path: Path, target_dir: Path) -> bool:
  target_name = target_dir.joinpath(source_path.name)
  if target_name.exists():
    return False
  urllib.request.urlretrieve(NOAA + str(source_path), target_name)
  add_margin(target_name, 0, 0, 50, 0)
  logger.info('%s saved', target_name)
  return True


def retrieve_files(enlil_file: Path, target_dir: Path) -> bool:
  if not download_with_etag(SOURCE_JSON, enlil_file):
    logging.info('No new version of %s', enlil_file.name)
    return False

  logging.info('New %s file has been downloaded: processing', enlil_file)
  with open(enlil_file, 'r', encoding='utf-8') as fdin:
    data_source = json.load(fdin)
    downloads = []
    for url in data_source:
      downloads.append(retrieve_image(Path(url['url']), target_dir))

  return any(downloads)


def purge(enlil_file: Path, target_dir: Path) -> None:
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


def counter(start: int = 1) -> Iterator[str]:
  cnt = start
  while True:
    yield f'{cnt:06d}'
    cnt += 1


def select_files(source_dir: Path) -> List[Path]:
  file_list = []
  for name in source_dir.glob('*.jpg'):
    file_list.append(name)
  logger.info('%d files selected for animation', len(file_list))
  return sorted(file_list)


def create_links(workdir: Path, file_list: List[Path]):
  cnt = counter()
  for filename in file_list:
    target = workdir.joinpath(f"enlil-{next(cnt)}.jpg")
    target.hardlink_to(filename)
    logger.debug('File "%s" selected', target)


def mk_video(work_dir: Path, video_file: Path):
  ffmpeg = shutil.which('ffmpeg')
  if not ffmpeg:
    logging.error('"ffmpeg" not found. Make sure it is correctly installed')
    return
  logfile = Path('/tmp/enlil_animation.log')
  tmp_file = work_dir.joinpath(f"{video_file}-{os.getpid()}.mp4")
  input_files = work_dir.joinpath('enlil-%06d.jpg')
  in_args = f'-y -framerate 10 -i {input_files}'.split()
  ou_args = '-an -c:v libx264 -pix_fmt yuv420p -vf scale=800:542'.split()
  cmd = [ffmpeg, *in_args, *ou_args, str(tmp_file)]

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
    tmp_file.rename(video_file)


def animate(source_dir: Path, video_file: Path):
  with Workdir(source_dir) as work_dir:
    files = select_files(source_dir)
    create_links(work_dir, files)
    mk_video(work_dir, video_file)


def mk_thumbnail(target_dir: Path) -> None:
  enlil_files = []
  for filename in target_dir.glob('enlil_*.jpg'):
    enlil_files.append((filename.stat().st_ctime, filename))
  enlil_files.sort()
  tn_source = enlil_files.pop()[1]
  latest = tn_source.with_name('latest.png')

  image = Image.open(tn_source)
  thumbnail = image.convert('RGB')
  thumbnail = thumbnail.resize((800, 450))

  if latest.exists():
    latest.unlink()
  thumbnail.save(latest, format="png")
  logger.info('Latest: %s', latest)


def main():
  logger.setLevel(logging.getLevelName(os.getenv('LOG_LEVEL', 'INFO')))

  parser = argparse.ArgumentParser(description='Generate ENLIL animation')
  parser.add_argument('-f', '--force', action="store_true", default=False,
                      help="Create the video, even if there is no new data")
  opts = parser.parse_args()

  config = read_config()

  if not config.target_dir.is_dir():
    logging.error('Directory "%s" does not exist', config.target_dir)
    raise SystemExit('Directory not found')

  if retrieve_files(config.enlil_file, config.target_dir) or opts.force:
    purge(config.enlil_file, config.target_dir)
    animate(config.target_dir, config.video_file)
    mk_thumbnail(config.target_dir)
  else:
    logger.info('Nothing to do; no new ENLIL images.')


if __name__ == "__main__":
  main()
