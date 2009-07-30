#!/usr/bin/env python

"""
    Purpose:
    Wordpress-to-Ikiwiki import tool

    Copyright:
    Copyright (C) 2007  Chris Lamb 

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

    Notes:
    I added some extra bits to include the [[!tag  foo]] stuff in the post,
    as it wasn't before, at all. I'll diff the versions out so you can see
    the mess I made :).

"""

import os, sys
import time
import re

from datetime import datetime
from BeautifulSoup import BeautifulSoup

import codecs, htmlentitydefs

codecs.register_error('html_replace', lambda x: (''.join([u'&%s;' \
    % htmlentitydefs.codepoint2name[ord(c)] for c in x.object[x.start:x.end]]), x.end))

def main(name, email, subdir, branch='master'):
    soup = BeautifulSoup(sys.stdin.read())

    # Regular expression to match stub in URL.
    stub_pattern = re.compile(r'.*\/(.+)\/$')

    for x in soup.findAll('item'):
        # Ignore draft posts
        if x.find('wp:status').string != 'publish': continue

        match = stub_pattern.match(x.guid.string)
        if match:
            stub = match.groups()[0]
        else:
            # Fall back to our own stubs
            stub = re.sub(r'[^a-zA-Z0-9_]', '-', x.title.string).lower()

        commit_msg = """Importing WordPress post "%s" [%s]""" % (x.title.string, x.guid.string)
        timestamp = time.mktime(time.strptime(x.find('wp:post_date_gmt').string, "%Y-%m-%d %H:%M:%S"))
        content = '[[!meta  title="%s"]]\n' % (x.title.string.replace('"', r'\"'))
        content += "[[!meta  date=\"%s\"]]\n" % datetime.fromtimestamp(timestamp)
        content += x.find('content:encoded').string.replace('\r\n', '\n')

        """
        We do it differently here because we have duplicates otherwise.
        Take a look:
        <category><![CDATA[Health]]></category>
        <category domain="category" nicename="health"><![CDATA[Health]]></category>

        If we do the what original did, we end up with all tags and cats doubled.
        Therefore we only pick out nicename="foo". Our 'True' below is our 'foo'.
        I'd much rather have the value of 'nicename', and tried, but my
        python skillz are extremely limited....
        """
        categories = x.findAll('category', nicename=True)
        if categories:
            content += "\n"
            for cat in categories:
                # remove 'tags/' because we have a 'tagbase' set.
                # your choice: 'tag', or 'taglink'
                content += "\n[[!tag  %s]]" % (cat.string.replace(' ', '-').lower())
                #content += "\n[[!taglink  %s]]" % (cat.string.replace(' ', '-'))
                # this is just debugging, and for fun
                # print >>sys.stderr, cat.string.replace(' ', '-')

        # moved this thing down
        data = content.encode('ascii', 'html_replace')
        print "commit refs/heads/%s" % branch
        print "committer %s <%s> %d +0000" % (name, email, timestamp)
        print "data %d" % len(commit_msg)
        print commit_msg
        print "M 644 inline %s" % os.path.join(subdir, "%s.mdwn" % stub)
        print "data %d" % len(data)
        print data

        i = 1
        for comment in x.findAll('wp:comment'):
            if comment.findAll('wp:comment_approved')[0].string != '1':
                continue

            author = comment.findAll('wp:comment_author')[0].string
            url = comment.findAll('wp:comment_author_url')[0].string
            date_str = comment.findAll('wp:comment_date_gmt')[0].string
            date = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%dT%H:%M:%SZ')
            content = comment.findAll('wp:comment_content')[0].string

            data = '''[[!comment format=mdwn
claimedauthor="%s"''' % author

            if url is not None:
                data += '''
url="%s"''' % url

            data += '''
subject="%s"
date="%s"
content="""
%s
"""]]''' % ('Re: ' + x.title.string.replace('"', r'\"'), date, content)
            print "M 644 inline %s" % os.path.join(subdir, stub, 'comment_%s._comment' % i)
            print "data %d" % len(data)
            try:
                print data.encode('ascii', 'replace')
            except:
                print >> sys.stderr, type(data)
                print >> sys.stderr, data

            i += 1

if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        print >>sys.stderr, "%s: usage: %s name email subdir [branch] < wordpress-export.xml | git-fast-import " % (sys.argv[0], sys.argv[0])
    else:
        main(*sys.argv[1:])
