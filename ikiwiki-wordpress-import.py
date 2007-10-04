#!/usr/bin/env python

import os, sys
import time
import re

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

        content = '[[meta title="%s"]]\n\n' % (x.title.string.replace('"', r'\"'))
        content += x.find('content:encoded').string.replace('\r\n', '\n')
        data = content.encode('ascii', 'html_replace')

        categories = x.findAll('category')
        if categories:
            content += "\n"
            for cat in categories:
                content += "\n[[tag tags/%s]]" % (cat.string.replace(' ', '-'))

        print "commit refs/heads/%s" % branch
        print "committer %s <%s> %d +0000" % (name, email, timestamp)
        print "data %d" % len(commit_msg)
        print commit_msg
        print "M 644 inline %s" % os.path.join(subdir, '%s.mdwn' % stub)
        print "data %d" % len(data)
        print data

if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        print >>sys.stderr, "%s: usage: %s name email subdir [branch] < wordpress-export.xml | git-fast-import " % (sys.argv[0], sys.argv[0])
    else:
        main(*sys.argv[1:])
