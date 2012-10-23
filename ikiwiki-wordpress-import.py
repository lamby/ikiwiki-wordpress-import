#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
    Purpose:
    Wordpress-to-Ikiwiki import tool

    Copyright:
    Copyright (C) 2007  Chris Lamb <lamby@debian.org>

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see .

    Usage: run --help as an argument with this script.
"""

import codecs
import htmlentitydefs
import os
import re
import sys
import time
from optparse import OptionParser
import logging
import subprocess
import urllib

from datetime import datetime
from BeautifulSoup import BeautifulSoup

logging.getLogger().setLevel(logging.INFO)

codecs.register_error('html_replace', lambda x: (''.join([u'&%s;' \
    % htmlentitydefs.codepoint2name[ord(c)] for c in x.object[x.start:x.end]]), x.end))

def main(opts, infile):
    soup = BeautifulSoup(infile.read())

    # step 1. slurp in wordpress items
    items = [Item(x) for x in soup.findAll('item')]

    # step 2. resolve sub-items and linked images
    post_id_map = dict((item.post_id, item) for item in items)
    for item in items:
        item.resolve_links(opts, post_id_map)

    # step 3. dump it out to git-fast-import format
    for item in items:
        if not item.parent and item.published:
            item.git_commit(opts)

    # step 4. write out a list of aliases
    git_commit_aliases(opts, items)

class Item(object):
    # Regular expression to match stub in URL.
    stub_pattern = re.compile(r'.*\/(.+)\/$')

    def __init__(self, x):
        self.x = x

        self.published = x.find('wp:status').string == 'publish' # Ignore draft posts
        self.post_type = x.find('wp:post_type').string

        self.title = x.title.string.strip()
        self.guid = x.guid.string
        self.link = urllib.unquote(x.link.string or x.link.next.string or "")
        self.post_id = int(x.find("wp:post_id").string)

        guid_match = self.stub_pattern.match(self.guid) if self.guid else None
        link_match = self.stub_pattern.match(self.link) if self.link else None
        if guid_match:
            self.stub = guid_match.group(1)
        elif link_match:
            self.stub = link_match.group(1)
        else:
            # Fall back to our own stubs
            self.stub = self.title.lower()

        # strip out any interesting characters
        self.stub = re.sub(r'[^a-zA-Z0-9_]', '-', self.stub)
        self.stub = re.sub(r'-+', '-', self.stub)

        if self.published:
            self.timestamp = time.mktime(time.strptime(x.find('wp:post_date_gmt').string, "%Y-%m-%d %H:%M:%S"))
        else:
            self.timestamp = None

        self.content = x.find('content:encoded').string.replace('\r\n', '\n').replace('\r', '\n')

        self.parent = None
        self.children = []
        self.new_path = None

        if self.post_type == "attachment":

            self.attach_path = x.find(lambda tag: tag.name == "wp:postmeta" and tag.find("wp:meta_key", text="_wp_attached_file")).find("wp:meta_value").string
            #self.attach_filename = os.path.split(x.find("wp:attachment_url").string)[1]
            self.attach_filename = os.path.split(self.attach_path)[1]
        else:
            self.attach_path = None
            self.attach_filename = None

    def get_markdown_content(self):
        x = self.x
        content = '[[!meta  title="%s"]]\n' % (x.title.string.replace('"', r"'"))
        content += "[[!meta  date=\"%s\"]]\n" % datetime.fromtimestamp(self.timestamp)
        content += "[[!meta  author=\"%s\"]]\n" % (x.find('dc:creator').string)
        content += self.content

        for tag in self.get_tags():
            # remove 'tags/' because we have a 'tagbase' set.
            # your choice: 'tag', or 'taglink'
            content += "\n[[!tag  %s]]" % (tag.replace('/', '-').lower())
            #content += "\n[[!taglink  %s]]" % (tag)

        return content

    def get_tags(self):
        # We do it differently here because we have duplicates otherwise.
        # Take a look:
        # <category><![CDATA[Health]]></category>
        # <category domain="category" nicename="health"><![CDATA[Health]]></category>
        #
        # If we do the what original did, we end up with all tags and cats doubled.
        # Therefore we only pick out nicename="foo". Our 'True' below is our 'foo'.
        # I'd much rather have the value of 'nicename', and tried, but my
        # python skillz are extremely limited....
        categories = self.x.findAll('category', nicename=True)
        if categories:
            for cat in categories:
                # this is just debugging, and for fun
                # logging.debug(cat.string.replace(' ', '-'))
                yield cat.string.replace(' ', '-')

    def get_comments(self):
        x = self.x
        for comment in x.findAll('wp:comment'):
            if comment.findAll('wp:comment_approved')[0].string != '1':
                continue

            author = comment.findAll('wp:comment_author')[0].string.replace('<![CDATA[', '').replace('<!]]>', '')
            url = comment.findAll('wp:comment_author_url')[0].string
            date_str = comment.findAll('wp:comment_date_gmt')[0].string
            date = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%dT%H:%M:%SZ')
            content = comment.findAll('wp:comment_content')[0].string.replace('<![CDATA[', '').replace('<!]]>', '')

            data = '''[[!comment format=mdwn
username="%s"''' % author

            if url is not None:
                data += '''
url="%s"''' % url

            data += '''
subject="%s"
date="%s"
content="""
%s
"""]]''' % ('Re: ' + x.title.string.replace('"', r'\"'), date, content)
            yield data

    def get_attachment_content(self, wp_uploads):
        if wp_uploads:
            filename = os.path.join(wp_uploads, self.attach_path)
            try:
                return open(filename, "rb").read()
            except IOError, e:
                logging.exception("Loading attachment %s" % filename)
        else:
            logging.warning("Can't import attachments unless --uploads-dir is provided")
        return ""

    def resolve_links(self, opts, post_id_map):
        soup = BeautifulSoup(self.content)

        self.resolve_parent(post_id_map)
        self.resolve_images(soup, opts.wp_uploads, post_id_map)

    def resolve_parent(self, post_id_map):
        parent = self.x.find("wp:post_parent").string or None
        self.parent = post_id_map.get(int(parent), None)

        if self.parent:
            self.parent.children.append(self)

            if self.x.find('wp:status').string == "inherit":
                self.published = self.parent.published

    def resolve_images(self, soup, wp_uploads, post_id_map):
        attachment_rel = re.compile(r"attachment wp-att-(\d+)")

        for imglink in soup.findAll("a", rel=attachment_rel):
            attachment_id = int(attachment_rel.match(imglink["rel"]).group(1))
            item = post_id_map.get(attachment_id, None)
            if item:
                img = imglink.find("img")
                if img:
                    imgargs = ["\"%s\"" % item.attach_filename]
                    if img.get("width") or img.get("height"):
                        imgargs.append("size=\"%sx%s\"" % (img.get("width"), img.get("height")))
                    if img.get("title"):
                        imgargs.append("title=\"%s\"" % img["title"])
                    if img.get("alt"):
                        imgargs.append("alt=\"%s\"" % img["alt"])
                    if img.get("class"):
                        imgargs.append("class=\"%s\"" % img["class"])
                    newtext = "[[!img  %s]]" % " ".join(imgargs)
                    imglink.replaceWith(newtext)

        self.content = unicode(soup)

    def git_commit(self, opts):
        x = self.x
        commit_msg = """Importing WordPress post "%s" [%s]""" % (self.title, self.guid)

        # moved this thing down
        commit = commit_msg.encode("utf_8")
        print "commit refs/heads/%s" % opts.branch
        print "committer %s <%s> %d +0000" % (opts.name, opts.email, self.timestamp)
        print "data %d" % len(commit)
        print commit
        self.git_commit_item(self, "", opts)

    def git_commit_item(self, item, subdir, opts):
        if item.post_type == "post":
            path = os.path.join(subdir, opts.subdir)
            item.git_commit_post(path)
        elif item.post_type == "page":
            path = os.path.join(subdir, opts.pagedir)
            item.git_commit_page(path)
        elif item.post_type == "attachment":
            path = subdir
            item.git_commit_attachment(path, opts.wp_uploads)

        for i, comment in enumerate(item.get_comments(), 1):
            item.git_commit_comment(path, comment, i)

        for child in item.children:
            item.git_commit_item(child, os.path.join(path, self.stub), opts)

    def git_commit_post(self, subdir):
        data = self.get_markdown_content().encode('utf_8', 'html_replace')

        print "M 644 inline %s" % os.path.join(subdir, "%s.mdwn" % self.stub)
        print "data %d" % len(data)
        print data

        self.new_path = os.path.join(subdir, self.stub)

    git_commit_page = git_commit_post

    def git_commit_attachment(self, subdir, wp_uploads):
        data = self.get_attachment_content(wp_uploads)
        filename = os.path.join(subdir, self.attach_filename)

        print "M 644 inline %s" % filename
        print "data %d" % len(data)
        print data

        self.new_path = filename

    def git_commit_comment(self, subdir, comment, i):
        data = comment.encode("utf_8")
        print "M 644 inline %s" % os.path.join(subdir, self.stub, 'comment_%s._comment' % i)
        print "data %d" % len(data)
        try:
            print data
        except Exception, e:
            logging.exception("%s:%s" % (type(data), data))

def git_commit_aliases(opts, items):
    rubbish = re.compile(r"^[a-z]+://[^/]+", re.I)
    def redirect(url, new_path):
        return "Redirect permanent %s %s" % (rubbish.sub("", url), new_path)

    redirects = ["# This can be a starting point for redirecting from your old URLs"] + \
                [redirect(item.guid, item.new_path) for item in items if item.new_path] + \
                [redirect(item.link, item.new_path) for item in items if item.link]
    redirects = "\n".join(redirects).encode("utf_8")
    commit = "Add example redirects for Apache"
    timestamp = time.mktime(datetime.now().timetuple())

    print "commit refs/heads/%s" % opts.branch
    print "committer %s <%s> %d +0000" % (opts.name, opts.email, timestamp)
    print "data %d" % len(commit)
    print commit
    print "M 644 inline initial-redirects.htaccess"
    print "data %d" % len(redirects)
    print redirects

def git_config(item):
    return subprocess.Popen(["git", "config", item],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            stdin=None).communicate()[0].strip()

if __name__ == "__main__":
    usage = "usage: %prog [options] < wordpress-export.xml | git-fast-import"
    parser = OptionParser(usage=usage)
    parser.add_option("-n", "--name", dest="name",
                      help="Committer name [default: %default]", metavar="NAME",
                      default=git_config("user.name"))
    parser.add_option("-e", "--email", dest="email",
                      help="Committer e-mail [default: %default]", metavar="ADDRESS",
                      default=git_config("user.email"))
    parser.add_option("-b", "--branch", dest="branch",
                      help="Branch to commit on [default: %default]", metavar="NAME",
                      default="master")
    parser.add_option("-p", "--posts", dest="subdir",
                      help="Sub-directory for blog posts [default: %default]",
                      default="posts", metavar="DIR")
    parser.add_option("-g", "--pages", dest="pagedir",
                      help="Sub-directory for pages [default: %default]",
                      default="pages", metavar="DIR")
    parser.add_option("-u", "--uploads-dir", dest="wp_uploads",
                      help="Location of wp-uploads",
                      metavar="DIR")

    (options, args) = parser.parse_args()

    if not options.name or not options.email:
        parser.error("You need to specify the git committer with --name and --email")

    if len(args) <= 1:
        main(options, sys.stdin if not args else open(args[0]))
    else:
        parser.error("too many command-line arguments")
