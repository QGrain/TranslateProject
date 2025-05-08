#!/usr/bin/env python
# Copyright 2025 QGrain <zhiyuzhang999@gmail.com>. All rights reserved.
# Use of this source code is governed by Apache 2 License that can be found in the LICENSE file.
# requirements:
# pip install PyYAML PyGithub

import os
import re
import sys
import yaml
import json
import argparse
from typing import List, Optional
from github import Github
from datetime import datetime
from time import time


class GithubAnalyzer:
    _cache = {}
    _cache_ttl = 86400 * 3
    _cache_file = os.path.expanduser('.github_analyzer_cache.json')
    # cache format: {cache_type: {cache_time: timestamp, cache_key: cache_value}}
    # cache_type: files, commits, ...

    def __init__(self, repo_path: str, gh_token: str = None):
        self.start_t = time()
        self.repo_path = repo_path
        self.token = gh_token or os.getenv('GITHUB_TOKEN')
        self.client = Github(gh_token) if gh_token else Github()
        self.repo_path = repo_path
        self.repo = None # we do not expect to get_repo in __init__ once we hace cache
        self._load_cache()

    def log(self, msg: str):
        """Log message with time duration"""
        elapsed_time = time() - self.start_t
        print(f"[{elapsed_time:.2f}s] {msg}")
    
    def _load_cache(self):
        """load cache from cache file"""
        if os.path.exists(self._cache_file):
            try:
                with open(self._cache_file, 'r') as f:
                    self._cache = json.load(f)
                self.log(f"Load cache from {self._cache_file} successfully")
            except Exception as e:
                self.log(f"Failed to load cache: {e}")
                self._cache = {}
    
    def _save_cache(self):
        try:
            with open(self._cache_file, 'w') as f:
                json.dump(self._cache, f, indent=4)
                self.log(f"Save cache to {self._cache_file} successfully")
        except Exception as e:
            self.log(f"Failed to save cache: {e}")
    
    def get_repo_files_recursively(self, target_path: str, exclude_dirs: List[str] = []) -> List:
        """Get all files under repo_path/target_path/ recursively"""
        cache_type = 'files'
        cache_key = str([target_path, exclude_dirs])
        current_time = time()
        
        # check if cache is valid and alive
        if cache_key in self._cache.get(cache_type, {}):
            cached_time = self._cache[cache_type]['cache_time']
            if current_time - cached_time < self._cache_ttl:
                cached_files = self._cache[cache_type][cache_key]
                return cached_files
        
        # if cache is expired or not found, fetch files from Github
        # notice: frequent request may exceed the rate limit
        if self.repo is None:
            self.log(f"Start to get_repo {self.repo_path}")
            self.repo = self.client.get_repo(self.repo_path)
        contents = self.repo.get_contents(target_path)
        files = []
        while contents:
            file_content = contents.pop(0)
            if file_content.type == 'dir' and file_content.path in exclude_dirs:
                continue
            if file_content.type == 'dir':
                # skip excluded directories, such as docs/translation/
                contents.extend(self.repo.get_contents(file_content.path))
            else:
                files.append(file_content.path)
        
        self.log(f"Get {len(files)} files from {self.repo_path}/{target_path} successfully")
        # update and save cache
        self._cache.setdefault(cache_type, {})['cache_time'] = current_time
        self._cache.setdefault(cache_type, {})[cache_key] = files
        self._save_cache()
        return files

    def get_files_commits_and_dates(self, local_file_list: List[str]) -> dict:
        """Get"""
        cache_type = 'commits'
        current_time = time()
        
        commits = self._cache.get(cache_type, {})
        # check if cache is valid and alive
        cached_time = commits.get('cache_time', 0)
        
        changed = False
        for local_file_path in local_file_list:
            if local_file_path in commits and current_time - cached_time < self._cache_ttl:
                continue
            changed = True
            commits[local_file_path] = {"collected": {}, "latest": {}}
            # get collected_commit_sha and collected_commit_date
            yaml_header = extract_frontmatter(local_file_path)
            try:
                collected_date_str = str(yaml_header['collected_date'])
                collected_date = datetime.strptime(collected_date_str, '%Y%m%d')
            except Exception as e:
                self.log(f"Failed to extract collected_date from YAML front matter: {e}")
                sys.exit(1)
            upstream_file_path = map_file_path(local_file_path, is_local2upstream=True)
            collected_commit_sha, collected_commit_date_str = self.get_file_commit_and_date(upstream_file_path, collected_date)
            commits[local_file_path]['collected'] = [collected_commit_sha, collected_commit_date_str]
            # get latest_commit_sha and latest_commit_date
            today_date = datetime.now()
            latest_commit_sha, latest_commit_date_str = self.get_file_commit_and_date(upstream_file_path, today_date)
            commits[local_file_path]['latest'] = [latest_commit_sha, latest_commit_date_str]
        
        # update and save cache
        self.log(f"Get {len(local_file_list)} files commits and dates from {self.repo_path} successfully")
        if changed == True:
            commits['cache_time'] = current_time
            self._cache[cache_type] = commits
            self._save_cache()
        else:
            self.log(f"Load commit cache from {self._cache_file} successfully")
        return commits
        
            
    def get_file_commit_and_date(self, upstream_file_path: str, until_date: Optional[datetime] = None) -> Optional[List[str]]:
        """Retrieve the latest commit for the specified file with until if any"""
        try:
            if self.repo is None:
                self.log(f"Start to get_repo {self.repo_path}")
                self.repo = self.client.get_repo(self.repo_path)
            commits = self.repo.get_commits(path=upstream_file_path, until=until_date)
            # get the latest commit
            latest_commit = next(iter(commits), None)
            commit_date = self.repo.get_commit(sha=latest_commit.sha).commit.author.date if latest_commit else None
            commit_date_str = commit_date.strftime('%Y-%m-%d %H:%M:%S') if commit_date else None
            self.log(f"[GET] File {upstream_file_path} until {until_date.strftime('%Y-%m-%d %H:%M:%S')} commit sha: {latest_commit.sha}, date: {commit_date_str}")
            return [latest_commit.sha, commit_date_str]
        except Exception as e:
            self.log(f"Failed to get {upstream_file_path} commit sha or date with until_date {until_date.strftime('%Y-%m-%d %H:%M:%S')}: {e}")
            return [None, None]


syzkaller_gh_analyzer = GithubAnalyzer('google/syzkaller')


def extract_frontmatter(md_file: str) -> Optional[dict]:
    """Extract YAML front matter from the collected markdown file"""
    with open(md_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # match and parse YAML front matter
    pattern = r'^---\s*\n(.*?)\n---\s*\n'
    match = re.match(pattern, content, re.DOTALL)
    if not match:
        syzkaller_gh_analyzer.log(f"YAML front matter not found in {md_file}")
        return None

    try:
        return yaml.safe_load(match.group(1))
    except yaml.YAMLError as e:
        syzkaller_gh_analyzer.log(f"Error parsing YAML in {md_file}: {e}")
        return None


def map_file_path(file_path: str, is_local2upstream: bool) -> str:
    """Map the file path to the corresponding source or translation file"""
    if is_local2upstream:
        mapped_file_path = file_path.replace('sources/syzkaller/', 'docs/')
        if mapped_file_path.startswith('./'):
            mapped_file_path = mapped_file_path[2:]
    else:
        mapped_file_path = file_path.replace('docs/', 'sources/syzkaller/')
    return mapped_file_path


def check_collection(hctt_proj_path: str = '.') -> List[str]:
    """"Check if the files in the syzkaller/docs/* are collected"""
    print('=' * 100)
    uncollected_files = []
    upstream_file_list = syzkaller_gh_analyzer.get_repo_files_recursively('docs/', exclude_dirs=['docs/translations'])
    no_need_to_collect = ['.drawio', '.patch', '.sh', '.py']
    
    # check if the existence of the files
    syzkaller_gh_analyzer.log(f"[+] Check Collection: checking {len(upstream_file_list)} files from upstream repo (ignore {no_need_to_collect})")
    for upstream_file_path in upstream_file_list:
        local_file_path = os.path.join(hctt_proj_path, map_file_path(upstream_file_path, is_local2upstream=False))
        if not os.path.isfile(local_file_path) and not any(upstream_file_path.endswith(ext) for ext in no_need_to_collect):
            syzkaller_gh_analyzer.log(f"File {local_file_path} not collected in {hctt_proj_path}")
            uncollected_files.append(upstream_file_path)
    return uncollected_files


def check_update(sources_syzkaller: str = 'sources/syzkaller') -> List[str]:
    """Check if the collected files are up to date with the syzkaller/docs/*"""
    print('=' * 100)
    need_update_files = []
    tranlatable_file_suffix = ['.md', '.txt']
    local_file_list = []
    for root, _, files in os.walk(sources_syzkaller):
        for fn in files:
            if any(fn.endswith(ext) for ext in tranlatable_file_suffix):
                local_file_list.append(os.path.join(root, fn))
    
    # check if the collected files need to be updated
    syzkaller_gh_analyzer.log(f"[+] Check Update: checking {len(local_file_list)} local files (endswith {tranlatable_file_suffix})")
    commits = syzkaller_gh_analyzer.get_files_commits_and_dates(local_file_list)
    for local_file_path, commit_info in commits.items():
        if local_file_path == 'cache_time':
            continue
        collected_commit_sha, collected_commit_date_str = commit_info['collected'][0], commit_info['collected'][1]
        latest_commit_sha, latest_commit_date_str = commit_info['latest'][0], commit_info['latest'][1]
        if collected_commit_sha != latest_commit_sha:
            syzkaller_gh_analyzer.log(f"File {local_file_path} is not up to date with upstream: collected_commit {collected_commit_date_str}, latest_commit {latest_commit_date_str}")
            need_update_files.append(local_file_path)
    return need_update_files


def main():    
    # suppose the script is run in the root of the project
    check_collection()
    check_update()

    syzkaller_gh_analyzer.log(f"Done!")

if __name__ == '__main__':
    main()