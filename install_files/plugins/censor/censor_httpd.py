#!/usr/bin/python

import time
import random
import string
import urllib
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
from urllib import unquote
from cgi import FieldStorage
import base64
from hashlib import sha1, sha512
from binascii import hexlify, unhexlify
from datetime import datetime
import os
import threading
import sqlite3
import socket
import nacl.signing
import codecs
import re
if __name__ == '__main__':
  import nntplib

class censor(BaseHTTPRequestHandler):

  def __init__(self, request, client_address, origin):
    self.origin = origin
    #if __name__ != '__main__':
    #  self.origin.log('postman initializing as plugin..', 2)
    #else:
    #  self.origin.log('postman initializing as standalone application..', 2)
    # ^ works
    BaseHTTPRequestHandler.__init__(self, request, client_address, origin)

  def do_POST(self):
    self.path = unquote(self.path)
    if self.path == '/moderate?evil':
      self.handle_moderation_request()
      return
    if self.path == '/moderate?auth':
      public = self.check_login() 
      if public:
        session = hexlify(self.origin.rnd.read(24))
        self.origin.sessions[session] = [int(time.time()) + 3600 * 6, public]
        self.send_redirect('/moderate/%s/' % session, 'access granted. this time.')
      else:
        self.send_login("totally not")
      return
    elif self.path.startswith("/moderate"):
      if self.path[10:58] not in self.origin.sessions:
        self.send_login()
        return
      self.session = self.path[10:58]
      self.root_path = self.path[:58] + '/' 
      if self.origin.sessions[self.session][0] < int(time.time()):
        self.send_login()
        return
      self.origin.sessions[self.session][0] = int(time.time()) + 3600 * 6
      path = self.path[58:]
      if path.startswith('/modify?'):
        key = path[8:]
        flags_available = int(self.origin.sqlite_censor.execute("SELECT flags FROM keys WHERE key=?", (self.origin.sessions[self.session][1],)).fetchone()[0])
        flag_required = int(self.origin.sqlite_censor.execute('SELECT flag FROM commands WHERE command="srnd-acl-mod"').fetchone()[0])
        if (flags_available & flag_required) != flag_required:
          self.send_redirect(self.root_path, "not authorized, flag srnd-acl-mod flag missing.<br />redirecting you in a moment.", 7)
          return
        try:
          self.handle_update_key(key)
          self.send_redirect(self.root_path, "update ok<br />redirecting you in a moment.", 4)
        except Exception as e:
          self.send_redirect(self.root_path, "update failed: %s<br />redirecting you in a moment." % e, 10)
      elif path == "/foo":
        self.die("POST bar")
      else:
        self.send_log()
      return
    self.origin.log("illegal access: {0}".format(self.path), 2)
    self.send_response(200)
    self.send_header('Content-type', 'text/plain')
    self.end_headers()
    self.wfile.write('nope')

  def do_GET(self):
    self.path = unquote(self.path)
    if self.path == '/moderate?getkey':
      secret = self.origin.rnd.read(32)
      public = nacl.signing.SigningKey(secret).verify_key.encode()
      self.send_error("secret: %s\npublic: %s" % (hexlify(secret), hexlify(public)))
      return
    elif self.path.startswith("/moderate"):
      if self.path[10:58] not in self.origin.sessions:
        self.send_login()
        return
      self.session = self.path[10:58]
      self.root_path = self.path[:58] + '/' 
      if self.origin.sessions[self.session][0] < int(time.time()):
        #del self.sessions[self.session]
        # FIXME: test ^
        self.send_login()
        return
      self.origin.sessions[self.session][0] = int(time.time()) + 3600 * 6
      path = self.path[58:]
      if path.startswith('/modify?'):
        key = path[8:]
        self.send_modify_key(key)
      elif path.startswith('/pic_log') or path.startswith("/piclog"):
        page = 1
        if '?' in path:
          try: page = int(path.split('?')[-1])
          except: pass
          if page < 1: page = 1 
        self.send_piclog(page)
      elif path.startswith('/message_log') or path.startswith('/messagelog'):
        self.send_messagelog()
      elif path.startswith('/stats'):
        self.send_stats()
      elif path.startswith('/settings'):
        self.send_settings()
      elif path.startswith('/showmessage?'):
        self.send_message(path[13:])
      elif path.startswith('/delete?'):
        self.handle_delete(path[8:])
      elif path.startswith('/restore?'):
        self.handle_restore(path[9:])
      elif path.startswith('/block_board?'):
        self.handle_block_board(path[13:])
      elif path.startswith('/unblock_board?'):
        self.handle_unblock_board(path[15:])
      elif path.startswith('/notimplementedyet'):
        self.send_error('not implemented yet')
      elif path == "/foo":
        self.die("GET bar")
      else:
        self.send_log()
      return
    self.origin.log("illegal access: {0}".format(self.path), 2)
    self.send_response(200)
    self.send_header('Content-type', 'text/plain')
    self.end_headers()
    self.wfile.write('nope')

  def handle_update_key(self, key):
    post_vars = FieldStorage(
      fp=self.rfile,
      headers=self.headers,
      environ={
        'REQUEST_METHOD':'POST',
        'CONTENT_TYPE':self.headers['Content-Type'],
      }
    )
    flags = post_vars.getlist("flags")
    result = 0
    if 'local_nick' in post_vars:
      local_nick = post_vars['local_nick'].value
    else:
      local_nick = ''
    for flag in flags:
      result += int(flag)
    self.origin.censor.add_article((self.origin.sessions[self.session][1], "srnd-acl-mod %s %i %s" % (key, result, local_nick)), "httpd")

  def check_login(self):
    current_date = int(time.time())
    todelete = list()
    for key in self.origin.sessions:
      if self.origin.sessions[key][0] <= current_date:
        todelete.append(key)
    for key in todelete:
      del self.origin.sessions[key]
    del todelete
    post_vars = FieldStorage(
      fp=self.rfile,
      headers=self.headers,
      environ={
        'REQUEST_METHOD':'POST',
        'CONTENT_TYPE':self.headers['Content-Type'],
      }
    )
    if not 'secret' in post_vars:
      return False
    if len(post_vars['secret'].value) != 64:
      return False
    try:
      public = hexlify(nacl.signing.SigningKey(unhexlify(post_vars['secret'].value)).verify_key.encode())
      flags_available = int(self.origin.sqlite_censor.execute("SELECT flags FROM keys WHERE key=?", (public,)).fetchone()[0])
      flag_required = int(self.origin.sqlite_censor.execute('SELECT flag FROM commands WHERE command="srnd-acl-view"').fetchone()[0])
      if (flags_available & flag_required) == flag_required:
        return public
      else:
        return False
    except Exception as e:
      self.origin.log(e, 0)
      return False

  def send_redirect(self, target, message, wait=0):
    self.send_response(200)
    self.send_header('Content-type', 'text/html')
    self.end_headers()
    self.wfile.write('<html><head><link type="text/css" href="/styles.css" rel="stylesheet"><META HTTP-EQUIV="Refresh" CONTENT="%i; URL=%s"></head><body class="mod"><center><br /><b>%s</b></center></body></html>' % (wait, target, message))
    return

  def send_login(self, message=""):
    self.send_response(200)
    self.send_header('Content-type', 'text/html')
    self.end_headers()
    self.wfile.write('<html><head><link type="text/css" href="/styles.css" rel="stylesheet"></head><body class="mod"><center>%s<br /><form action="/moderate?auth" enctype="multipart/form-data" method="POST"><label>your secret</label>&nbsp;<input type="text" name="secret" style="width: 400px;"/><input type="submit" /></form></html></html>' % message)

  def send_modify_key(self, key):
    out = self.origin.template_modify_key
    flags = self.origin.sqlite_censor.execute("SELECT command, flag FROM commands").fetchall()
    cur_template = self.origin.template_log_flagnames
    #table = list()
    #for flag in flags:
    #  current_flag = flag[0]
    #  if "-" in current_flag:
    #    current_flag = current_flag.split("-", 1)[1]
    #  table.append(cur_template.replace("%%flag%%", current_flag))
    #out = out.replace("%%flag_names%%", "\n".join(table))
    #del table[:]
    
    flaglist = list()
    row = self.origin.sqlite_censor.execute("SELECT key, local_name, flags, id FROM keys WHERE key = ?", (key,)).fetchone()
    if not row:
      self.die("key not found")
      return
    flagset_template = self.origin.template_modify_key_flagset
    out = out.replace("%%key%%", row[0])
    out = out.replace("%%nick%%", row[1])
    counter = 0
    for flag in flags:
      counter += 1
      if (int(row[2]) & int(flag[1])) == int(flag[1]):
        checked = 'checked="checked"'  
      else:
        checked = ''
      cur_template = flagset_template.replace("%%flag%%", flag[1])
      cur_template = cur_template.replace("%%flag_name%%", flag[0])
      cur_template = cur_template.replace("%%checked%%", checked)
      if counter == 5:
        cur_template += "<br />"
      else:
        cur_template += "&nbsp;"
      flaglist.append(cur_template)
    out = out.replace("%%modify_key_flagset%%", "".join(flaglist))
    del flaglist[:]
    self.send_response(200)
    self.send_header('Content-type', 'text/html')
    self.end_headers()
    self.wfile.write(out)    

  def send_log(self):
    out = self.origin.template_log
    out = out.replace('%%navigation%%', ''.join(self.__get_navigation('moderation_log')))
    table = list()    
    for row in self.origin.sqlite_censor.execute("SELECT key, local_name, command, data, reason, comment, timestamp FROM log, commands, keys, reasons WHERE log.accepted = 1 AND keys.id = log.key_id AND commands.id = log.command_id AND reasons.id = log.reason_id ORDER BY log.id DESC").fetchall():
      cur_template = self.origin.template_log_accepted
      if row[1] != "":
        cur_template = cur_template.replace("%%key_or_nick%%", row[1])
      else:
        cur_template = cur_template.replace("%%key_or_nick%%", row[0])
      cur_template = cur_template.replace("%%key%%", row[0])
      cur_template = cur_template.replace("%%action%%", row[2])
      cur_template = cur_template.replace("%%reason%%", row[4])
      cur_template = cur_template.replace("%%date%%", datetime.utcfromtimestamp(row[6]).strftime('%Y/%m/%d %H:%M'))
      if row[2] != 'delete' and row[2] != 'overchan-delete-attachment':
        cur_template = cur_template.replace("%%postid%%", row[3].replace("<", "&lt;").replace(">", "&gt;"))
        cur_template = cur_template.replace("%%restore_link%%", '')
        cur_template = cur_template.replace("%%delete_link%%", '')
      else:
        message_id = row[3].replace("<", "&lt;").replace(">", "&gt;")
        try:
          if os.stat(os.path.join('articles', 'censored', row[3])).st_size > 0:
            cur_template = cur_template.replace("%%postid%%", '<a href="showmessage?%s" target="_blank">%s</a>' % (message_id, message_id))
            if row[2] == 'delete' or row[2] == 'overchan-delete-attachment':
              cur_template = cur_template.replace("%%restore_link%%", '<a href="restore?%s">restore</a>&nbsp;' % message_id)
            else:
              cur_template = cur_template.replace("%%restore_link%%", '')
            cur_template = cur_template.replace("%%delete_link%%", '<a href="delete?%s">delete</a>&nbsp;' % message_id)
          else:
            cur_template = cur_template.replace("%%postid%%", message_id)
            cur_template = cur_template.replace("%%restore_link%%", '')
            cur_template = cur_template.replace("%%delete_link%%", '')
        except:
          if os.path.isfile(os.path.join('articles', row[3])):
            cur_template = cur_template.replace("%%postid%%", message_id)
            item_row = self.origin.sqlite_overchan.execute('SELECT parent FROM articles WHERE article_uid = ?', (row[3],)).fetchone()
            if item_row:
              if item_row[0] == '':
                cur_template = cur_template.replace("%%restore_link%%", '<a href="/thread-%s.html" target="_blank">restored</a>&nbsp;' % sha1(row[3]).hexdigest()[:10])
              else:
                cur_template = cur_template.replace("%%restore_link%%", '<a href="/thread-%s.html#%s" target="_blank">restored</a>&nbsp;' % (sha1(item_row[0]).hexdigest()[:10], sha1(row[3]).hexdigest()[:10]))
            else:
              cur_template = cur_template.replace("%%restore_link%%", 'restored&nbsp;')
            cur_template = cur_template.replace("%%delete_link%%", '')
      table.append(cur_template)
    out = out.replace("%%mod_accepted%%", "\n".join(table))
    del table[:]    
    for row in self.origin.sqlite_censor.execute("SELECT key, local_name, command, data, reason, comment, timestamp FROM log, commands, keys, reasons WHERE log.accepted = 0 AND keys.id = log.key_id AND commands.id = log.command_id AND reasons.id = log.reason_id ORDER BY log.id DESC").fetchall():
      cur_template = self.origin.template_log_ignored
      if row[1] != "":
        cur_template = cur_template.replace("%%key_or_nick%%", row[1])
      else:
        cur_template = cur_template.replace("%%key_or_nick%%", '%s[..]%s' % (row[0][:6], row[0][-6:]))
      cur_template = cur_template.replace("%%key%%", row[0])
      cur_template = cur_template.replace("%%action%%", row[2])
      message_id = row[3].replace("<", "&lt;").replace(">", "&gt;")
      try:
        if os.stat(os.path.join('articles', row[3])).st_size > 0:
          cur_template = cur_template.replace("%%postid%%", '<a href="showmessage?%s" target="_blank">%s</a>' % (message_id, message_id))
        else:
          cur_template = cur_template.replace("%%postid%%", message_id)
      except:
        cur_template = cur_template.replace("%%postid%%", message_id)
      cur_template = cur_template.replace("%%reason%%", row[4])
      cur_template = cur_template.replace("%%date%%", datetime.utcfromtimestamp(row[6]).strftime('%Y/%m/%d %H:%M'))
      table.append(cur_template)
    out = out.replace("%%mod_ignored%%", "\n".join(table))
    del table[:]
    flags = self.origin.sqlite_censor.execute("SELECT command, flag FROM commands").fetchall()
    cur_template = self.origin.template_log_flagnames
    for flag in flags:
      current_flag = flag[0]
      if "-" in current_flag:
        current_flag = current_flag.split("-", 1)[1]
      table.append(cur_template.replace("%%flag%%", current_flag))
    out = out.replace("%%flag_names%%", "\n".join(table))
    del table[:]
    flagset_template = self.origin.template_log_flagset
    flaglist = list()
    for row in self.origin.sqlite_censor.execute("SELECT key, local_name, flags, id FROM keys WHERE flags != 0").fetchall():
      cur_template = self.origin.template_log_whitelist
      cur_template = cur_template.replace("%%key%%", row[0])
      cur_template = cur_template.replace("%%nick%%", row[1])
      #table.append(self.origin.template_log_flagset)
      for flag in flags:
        if (int(row[2]) & int(flag[1])) == int(flag[1]):
          flaglist.append(flagset_template.replace("%%flag%%", "y"))
        else:
          flaglist.append(flagset_template.replace("%%flag%%", "n"))
      cur_template = cur_template.replace("%%flagset%%", "\n".join(flaglist))
      del flaglist[:]
      count = self.origin.sqlite_censor.execute("SELECT count(data) FROM log WHERE key_id = ?", (row[3],)).fetchone()
      cur_template = cur_template.replace("%%count%%", str(count[0]))
      table.append(cur_template)
    out = out.replace("%%mod_whitelist%%", "\n".join(table))
    del table[:]
    for row in self.origin.sqlite_censor.execute("SELECT key, local_name, flags, id FROM keys WHERE flags = 0").fetchall():
      cur_template = self.origin.template_log_unknown
      cur_template = cur_template.replace("%%key%%", row[0])
      cur_template = cur_template.replace("%%nick%%", row[1])
      count = self.origin.sqlite_censor.execute("SELECT count(data) FROM log WHERE key_id = ?", (row[3],)).fetchone()
      cur_template = cur_template.replace("%%count%%", str(count[0]))
      table.append(cur_template)
    out = out.replace("%%mod_unknown%%", "\n".join(table))
    self.send_response(200)
    self.send_header('Content-type', 'text/html')
    self.end_headers()
    self.wfile.write(out)
    
  def send_piclog(self, page=1):
    #out = '<html><head><link type="text/css" href="/styles.css" rel="stylesheet"><style type="text/css">body { margin: 10px; margin-top: 20px; font-family: monospace; font-size: 9pt; } .navigation { background: #101010; padding-top: 19px; position: fixed; top: 0; width: 100%; }</style></head><body>%%navigation%%'
    out = '<html><head><title>piclog</title><link type="text/css" href="/styles.css" rel="stylesheet"></head>\n<body class="mod">\n%%navigation%%\n'
    pagination = '<div style="float:right;">'
    if page > 1:
      pagination += '<a href="pic_log?%i">previous</a>' % (page-1)
    else:
      pagination += 'previous'
    pagination += '&nbsp;<a href="pic_log?%i">next</a></div>' % (page+1)
    out = out.replace("%%navigation%%", ''.join(self.__get_navigation('pic_log', add_after=pagination)))
    table = list()
    table.append(out.replace("%%pagination%%", pagination))
    #self.wfile.write(out)
    for row in self.origin.sqlite_overchan.execute('SELECT * FROM (SELECT thumblink, parent, article_uid, last_update, sent FROM articles WHERE thumblink != "" AND thumblink != "invalid" AND thumblink != "document" ORDER BY last_update DESC) ORDER by sent DESC LIMIT ?, 30', ((page-1)*30,)).fetchall():
      cur_template = '<a href="/%%target%%" target="_blank"><img src="%%thumblink%%" class="image" style="height: 200px;" /></a>'
      if row[1] == '' or row[1] == row[2]:
        target = 'thread-%s.html' % sha1(row[2]).hexdigest()[:10]
      else:
        target = 'thread-%s.html#%s' % (sha1(row[1]).hexdigest()[:10], sha1(row[2]).hexdigest()[:10])
      cur_template = cur_template.replace("%%target%%", target)
      cur_template = cur_template.replace("%%thumblink%%", '/thumbs/' + row[0])
      table.append(cur_template)
    table.append('<br />' + pagination + '<br />')
    table.append('</body></html>')
    self.send_response(200)
    self.send_header('Content-type', 'text/html')
    self.end_headers()
    self.wfile.write("\n".join(table))
    
  def send_messagelog(self, page=0):
    table = list()
    #out = u'<html><head><meta http-equiv="content-type" content="text/html; charset=utf-8"><link type="text/css" href="/styles.css" rel="stylesheet"><style type="text/css">table { font-size: 9pt;} td { vertical-align: top; } .dontwrap { white-space: nowrap; } body { margin: 10px; margin-top: 20px; font-family: monospace; font-size: 9pt; } .navigation { background: #101010; padding-top: 19px; position: fixed; top: 0; width: 100%; }</style></head><body>%%navigation%%%%content%%</body></html>'
    out = u'<html><head><meta http-equiv="content-type" content="text/html; charset=utf-8"><title>messagelog</title><link type="text/css" href="/styles.css" rel="stylesheet"></head><body class="mod">%%navigation%%%%content%%</body></html>'
    out = out.replace("%%navigation%%", ''.join(self.__get_navigation('message_log'))) 
    #for row in self.origin.sqlite_overchan.execute('SELECT article_uid, parent, sender, subject, message, parent, public_key, sent, group_name FROM articles, groups WHERE groups.group_id = articles.group_id ORDER BY articles.sent DESC LIMIT ?,50', (50*page,)).fetchall():
    for row in self.origin.sqlite_overchan.execute('SELECT article_uid, parent, sender, subject, message, parent, public_key, sent, group_name FROM articles, groups WHERE groups.group_id = articles.group_id ORDER BY articles.sent DESC LIMIT ?,100', (0,)).fetchall():
      if row[1] == '' or row[1] == row[0]:
        # parent
        link = "thread-%s.html" % sha1(row[0]).hexdigest()[:10]
      else:
        link = "thread-%s.html#%s" % (sha1(row[1]).hexdigest()[:10], sha1(row[0]).hexdigest()[:10])
      sender = row[2]
      subject = row[3]
      message = row[4]
      if len(sender) > 15:
        sender = sender[:15]+ ' [..]'
      if len(subject) > 45:
        subject = subject[:45] + ' [..]'
      if len(message) > 200:
        message = message[:200] + "\n[..]"
      subject = self.origin.breaker.sub(self.__breakit, subject)
      message = self.origin.breaker.sub(self.__breakit, message)
      table.append(u'<tr><td class="dontwrap">%s</td><td class="dontwrap">%s</td><td>%s</td><td><a href="/%s" target="_blank">%s</a></td><td class="message_span">%s</td></tr>' % (datetime.utcfromtimestamp(row[7]).strftime('%Y/%m/%d %H:%M'), row[8], sender, link, subject, message))
    out = out.replace("%%content%%", '<table class="datatable"><tr><th>sent</th><th>board</th><th>sender</th><th>subject</th><th>message</th></tr>\n' + '\n'.join(table))
    self.send_response(200)
    self.send_header('Content-type', 'text/html')
    self.end_headers()
    self.wfile.write(out.encode('UTF-8'))

  def send_stats(self, page=0):
    #out = u'<html><head><meta http-equiv="content-type" content="text/html; charset=utf-8"><link type="text/css" href="/styles.css" rel="stylesheet"><style type="text/css">table { font-size: 9pt;} td { vertical-align: top; } .top2 { float: left; }.float { float: left; margin-right: 10px; margin-bottom: 10px; } .right { text-align: right; padding-right: 5px; } body { margin: 10px; margin-top: 20px; font-family: monospace; font-size: 9pt; } .navigation { background: #101010; padding-top: 19px; position: fixed; top: 0; width: 100%; }</style></head><body>%%navigation%%%%content%%</body></html>'
    out = u'<html><head><meta http-equiv="content-type" content="text/html; charset=utf-8"><title>stats</title><link type="text/css" href="/styles.css" rel="stylesheet"><style type="text/css">.top2 { float: left; } .float { float: left; margin-right: 10px; margin-bottom: 10px; }</style></head><body class="mod">%%navigation%%%%content%%</body></html>'
    out = out.replace("%%navigation%%", ''.join(self.__get_navigation('stats')))
    template_2_rows = '<tr><td class="right">%s</td><td>%s</td></tr>'
    template_3_rows = '<tr><td>%s</td><td class="right">%s</td><td>%s</td></tr>'
    template_4_rows = '<tr><td>%s</td><td class="right">%s</td><td>%s</td><td>%s</td></tr>'
    out_table = list()

    out_table.append('<div class="top1">')
    #out_table.append('<table border="1" cellspacing="0" class="float">\n<tr><th>date</th><th>posts</th><th>frontend</th><th>&nbsp;</th></tr>')
    #for item in self.__stats_usage_by_frontend(7, 30):
    #  out_table.append(template_4_rows % item)
    #out_table.append('</table>')
    out_table.append('<div class="float">')
    out_table.append('<table class="datatable">\n<tr><th>date</th><th>posts</th><th>&nbsp;</th></tr>')
    for item in self.__stats_usage(31, 30):
      out_table.append(template_3_rows % item)
    out_table.append('</table>')
    out_table.append('</div>')
    out_table.append('<div class="float">')
    out_table.append('<table class="datatable">\n<tr><th>posts</th><th>frontend</th></tr>')
    for item in self.__stats_frontends():
      out_table.append(template_2_rows % item)
    out_table.append('</table>')
    out_table.append('</div>')
    out_table.append('<div class="float">')
    out_table.append('<table class="datatable">\n<tr><th>posts</th><th>group</th></tr>')
    for item in self.__stats_groups():
      out_table.append(template_2_rows % item)
    out_table.append('</table>')
    out_table.append('</div>')
    out_table.append('<div class="float">')
    out_table.append('<table class="datatable">\n<tr><th>month</th><th>posts</th><th>&nbsp;</th></tr>')
    for item in self.__stats_usage_month(30):
      out_table.append(template_3_rows % item)
    out_table.append('</table>')
    out_table.append('</div>')
    out_table.append('</div>')
    
    out_table.append('<div class="top2">')
    out_table.append('<div class="float">')
    out_table.append('<table class="datatable">\n<tr><th>weekday</th><th>posts</th><th>(last 28 days average)</th></tr>')
    for item in self.__stats_usage_weekday(28):
      out_table.append(template_3_rows % item)
    out_table.append('</table>')
    out_table.append('</div>')
    out_table.append('<div class="float">')
    out_table.append('<table class="datatable">\n<tr><th>weekday</th><th>posts</th><th>(totals)</th></tr>')
    for item in self.__stats_usage_weekday():
      out_table.append(template_3_rows % item)
    out_table.append('</table>')
    out_table.append('</div>')
    out_table.append('</div>')
    
    out = out.replace('%%content%%', '\n'.join(out_table))
    del out_table
    self.send_response(200)
    self.send_header('Content-type', 'text/html')
    self.end_headers()
    self.wfile.write(out.encode('UTF-8'))

  def send_message(self, message_id):
    self.send_response(200)
    self.send_header('Content-type', 'text/html')
    self.end_headers()
    #out = '<html><head><link type="text/css" href="/styles.css" rel="stylesheet"><style type="text/css">body { margin: 10px; margin-top: 20px; font-family: monospace; font-size: 9pt; } .navigation { background: #101010; padding-top: 19px; position: fixed; top: 0; width: 100%; }</style></head><body>%%navigation%%<pre>'
    out = '<html><head><title>view message</title><link type="text/css" href="/styles.css" rel="stylesheet"></head><body class="mod">%%navigation%%<pre>'
    out = out.replace("%%navigation%%", ''.join(self.__get_navigation('')))
    self.wfile.write(out.encode('UTF-8'))

    if os.path.isfile(os.path.join('articles', 'censored', message_id)):
      f = codecs.open(os.path.join('articles', 'censored', message_id), 'r', 'UTF-8')
      self.__write_nntp_article(f)
    elif os.path.isfile(os.path.join('articles', message_id)):
      f = codecs.open(os.path.join('articles', message_id), 'r', 'UTF-8')
      self.__write_nntp_article(f)
    else:
      self.wfile.write('message_id \'%s\' not found' % message_id.replace('<', '&lt;').replace('>', '&gt;'))
    self.wfile.write('</pre></body></html>')
    
  def send_settings(self):
    #out = '<html><head><link type="text/css" href="/styles.css" rel="stylesheet"><style type="text/css">body { margin: 10px; margin-top: 20px; font-family: monospace; font-size: 9pt; } .navigation { background: #101010; padding-top: 19px; position: fixed; top: 0; width: 100%; }</style></head><body>%%navigation%%<table class="datatable" id="groups" cellspacing="0" cellpadding="0"><tr><th>posts</th><th>board</th><th>&nbsp;</th></tr>%%boards%%</table></body></html>'
    out = '<html><head><title>settings</title><link type="text/css" href="/styles.css" rel="stylesheet"></head><body class="mod">%%navigation%%<table class="datatable" id="groups"><tr><th>posts</th><th>board</th><th>&nbsp;</th></tr>%%boards%%</table></body></html>'
    out = out.replace("%%navigation%%", ''.join(self.__get_navigation('settings')))
    template_unblocked = '<tr><td class="right">%s</td><td>%s</td><td><a href="block_board?%i">block</a></td></tr>'
    template_blocked = '<tr><td class="right">%s</td><td>%s</td><td><a href="unblock_board?%i">unblock</a></td></tr>'
    stats = list()
    for item in self.__stats_groups(ids=True, status=True):
      if item[3] == 0:
        stats.append(template_unblocked % item[:3])
      else:
        stats.append(template_blocked % item[:3])
    out = out.replace("%%boards%%", "\n".join(stats))
    self.send_response(200)
    self.send_header('Content-type', 'text/html')
    self.end_headers()
    self.wfile.write(out.encode('UTF-8'))
    
  def handle_delete(self, message_id):
    path = os.path.join('articles', 'censored', message_id)
    try:
      if os.stat(path).st_size > 0:
        f = open(path, 'w')
        f.close()
    except:
      pass
    self.send_redirect(self.root_path, "redirecting", 0)
    
  def handle_restore(self, message_id):
    if os.path.isfile(os.path.join('articles', 'censored', message_id)):
      os.rename(os.path.join('articles', 'censored', message_id), os.path.join('incoming', message_id + '_'))
      f = open(os.path.join('articles', 'restored', message_id), 'w')
      f.close()
      self.send_redirect(self.root_path, "redirecting", 0)
    else:
      self.send_redirect(self.root_path, 'message_id does not exist in articles/censored', 5)

  def handle_block_board(self, board_id):
    try:
      board_name = self.origin.sqlite_overchan.execute('SELECT group_name FROM groups WHERE group_id = ?', (int(board_id),)).fetchone()[0]
      self.origin.censor.add_article((self.origin.sessions[self.session][1], "overchan-board-del %s" % (board_name)), "httpd")
      self.send_redirect(self.root_path + 'settings', "redirecting. it might take a second to synchronize.", 3)
    except Exception as e:
      self.send_redirect(self.root_path + 'settings', "could not block board: %s" % e, 10)

  def handle_unblock_board(self, board_id):
    try:
      board_name = self.origin.sqlite_overchan.execute('SELECT group_name FROM groups WHERE group_id = ?', (int(board_id),)).fetchone()[0]
      self.origin.censor.add_article((self.origin.sessions[self.session][1], "overchan-board-add %s" % (board_name)), "httpd")
      self.send_redirect(self.root_path + 'settings', "redirecting. it might take a second to synchronize.", 3)
    except Exception as e:
      self.send_redirect(self.root_path + 'settings', "could not unblock board: %s" % e, 10)

  def send_error(self, errormessage):
    self.send_response(200)
    self.send_header('Content-type', 'text/plain')
    self.end_headers()
    self.wfile.write(errormessage)

  def __write_nntp_article(self, f):
    attachment = re.compile('^[cC]ontent-[tT]ype: ([a-zA-Z0-9/]+).*name="([^"]+)')
    attachment_details = None
    base64 = False
    writing_base64 = False
    for line in f:
      if line.lower().startswith('content-type:'):
        attachment_details = attachment.match(line)
      elif line.lower().startswith('content-transfer-encoding: base64'):
        base64 = True
      if len(line) == 1:
        if base64 == True and attachment_details != None:
          self.wfile.write('\n<img src="data:%s;base64,' % attachment_details.group(1))
          writing_base64 = True
        else:
          self.wfile.write(line)
      elif writing_base64 and line.startswith('--'):
        self.wfile.write('" title="%s" width="100%%" />\n' % attachment_details.group(2).replace('<', '&lt;').replace('>', '&gt;').encode('UTF-8'))
        writing_base64 = False
        base64 = False
      elif writing_base64:
        self.wfile.write(line.encode('UTF-8'))
      else:
        self.wfile.write(line.replace('<', '&lt;').replace('>', '&gt;').encode('UTF-8'))
    f.close()
    if writing_base64:
      self.wfile.write('" title="%s" />\n' % attachment_details.group(2).replace('<', '&lt;').replace('>', '&gt;'))

  def __stats_frontends(self):
    hosts = list()
    try:
      for row in self.origin.sqlite_overchan.execute('SELECT count(1) as counter, rtrim(substr(article_uid, instr(article_uid, "@") + 1), ">") as hosts FROM articles GROUP by hosts ORDER BY counter DESC').fetchall():
        hosts.append((row[0], row[1]))
    except:
      # work around old sqlite3 version without instr() support:
      #  - remove all printable ASCII chars but " @ and > from the left
      #  - remove all printable ASCII chars but ' @ and > from the left
      #  - remove @ from the left
      #  - remove > from the right
      #  - group by result
      for row in self.origin.sqlite_overchan.execute('SELECT count(1) as counter, rtrim(ltrim(ltrim(ltrim(article_uid, " !#$%&\'()*+,-./0123456789:;<=?ABCDEFGHIJKLMNOPQRSTUVWXYZ[\]^_`abcdefghijklmnopqrstuvwxyz{|}~"), \' !"#$%&()*+,-./0123456789:;<=?ABCDEFGHIJKLMNOPQRSTUVWXYZ[\]^_`abcdefghijklmnopqrstuvwxyz{|}~\'), "@"), ">") as hosts FROM articles GROUP by hosts ORDER BY counter DESC').fetchall():
        hosts.append((row[0], row[1]))        
    return hosts

  def __stats_groups(self, ids=False, status=False):
    groups = list()
    for row in self.origin.sqlite_overchan.execute('SELECT count(1) as counter, group_name, groups.group_id, blocked FROM articles, groups WHERE articles.group_id = groups.group_id GROUP BY articles.group_id ORDER BY counter DESC').fetchall():
      if ids and status:
        groups.append((row[0], row[1], row[2], row[3]))
      elif ids:
        groups.append((row[0], row[1], row[2]))
      elif status:
        groups.append((row[0], row[1], row[3]))
      else:
        groups.append((row[0], row[1]))
    return groups

  def __stats_usage_by_frontend(self, days=7, bar_length=29):    
    stats = list()
    totals = int(self.origin.sqlite_overchan.execute('SELECT count(1) FROM articles WHERE sent > strftime("%s", "now", "-' + str(days) + ' days")').fetchone()[0])
    stats.append(('all posts', totals, '&nbsp;', 'in previous %s days' % days))
    max = 0
    for row in self.origin.sqlite_overchan.execute('SELECT count(1) as counter, strftime("%Y-%m-%d",  sent, "unixepoch") as day, strftime("%w", sent, "unixepoch") as weekday, rtrim(substr(article_uid, instr(article_uid, "@") + 1), ">") as host FROM articles WHERE sent > strftime("%s", "now", "-' + str(days) + ' days") GROUP BY day, host ORDER BY day DESC').fetchall():
      if row[0] > max: max = row[0]
      stats.append((row[0], row[1], self.origin.weekdays[int(row[2])], row[3]))
    for index in range(1, len(stats)):
      graph = ''
      for x in range(0, int(float(stats[index][0])/max*bar_length)):
        graph += '='
      if len(graph) == 0:
        graph = '&nbsp;'
      stats[index] = ('<span title="%s">%s</span>' % (stats[index][2], stats[index][1]), stats[index][0], stats[index][3], graph)
    return stats

  def __stats_usage(self, days=30, bar_length=29):
    stats = list()
    totals = int(self.origin.sqlite_overchan.execute('SELECT count(1) FROM articles WHERE sent > strftime("%s", "now", "-31 days")').fetchone()[0])
    stats.append(('all posts', totals, 'in previous %s days' % 31))
    max = 0
    for row in self.origin.sqlite_overchan.execute('SELECT count(1) as counter, strftime("%Y-%m-%d",  sent, "unixepoch") as day, strftime("%w", sent, "unixepoch") as weekday FROM articles WHERE sent > strftime("%s", "now", "-31 days") GROUP BY day ORDER BY day DESC').fetchall():
      if row[0] > max: max = row[0]
      stats.append((row[0], row[1], self.origin.weekdays[int(row[2])]))
    for index in range(1, len(stats)):
      graph = ''
      for x in range(0, int(float(stats[index][0])/max*bar_length)):
        graph += '='
      if len(graph) == 0:
        graph = '&nbsp;'
      stats[index] = ('<span title="%s">%s</span>' % (stats[index][2], stats[index][1]), stats[index][0], graph)
    return stats

  def __stats_usage_month(self, bar_length=29):
    stats = list()
    totals = int(self.origin.sqlite_overchan.execute('SELECT count(1) FROM articles').fetchone()[0])
    stats.append(('all posts', totals, 'since beginning'))
    max = 0
    for row in self.origin.sqlite_overchan.execute('SELECT count(1) as counter, strftime("%Y-%m",  sent, "unixepoch") as month FROM articles GROUP BY month ORDER BY month DESC').fetchall():
      if row[0] > max: max = row[0]
      stats.append((row[0], row[1]))
    for index in range(1, len(stats)):
      graph = ''
      for x in range(0, int(float(stats[index][0])/max*bar_length)):
        graph += '='
      if len(graph) == 0:
        graph = '&nbsp;'
      stats[index] = (stats[index][1], stats[index][0], graph)  
    return stats
  
  def __stats_usage_weekday(self, days=None, bar_length=29):
    if days:
      if days % 7 != 0:
        raise Exception("days has to be a multiple of 7 or None")
      result = self.origin.sqlite_overchan.execute('SELECT count(1) as counter, strftime("%w",  sent, "unixepoch") as weekday FROM articles WHERE sent > strftime("%s", "now", "-' + str(days) + ' days") GROUP BY weekday ORDER BY weekday ASC').fetchall()
    else:
      result = self.origin.sqlite_overchan.execute('SELECT count(1) as counter, strftime("%w",  sent, "unixepoch") as weekday FROM articles GROUP BY weekday ORDER BY weekday ASC').fetchall()
    stats = list()
    max = 0 
    for row in result:
      if days:
        avg = float(row[0]) / (days / 7)
        if avg > max: max = avg
        stats.append((avg, self.origin.weekdays[int(row[1])]))
      else:
        if row[0] > max: max = row[0]
        stats.append((row[0], self.origin.weekdays[int(row[1])]))
    for index in range(0, len(stats)):
      graph = ''
      for x in range(0, int(float(stats[index][0])/max*bar_length + 0.5)):
        graph += '='
      if len(graph) == 0:
        graph = '&nbsp;'
      if days:
        stats[index] = (stats[index][1], "%.2f" % stats[index][0], graph)
      else:
        stats[index] = (stats[index][1], stats[index][0], graph)
    stats.append(stats[0])
    return stats[1:]

  def __get_navigation(self, current, add_after=None):
    out = list()
    #out.append('<div class="navigation">')
    for item in (('moderation_log', 'moderation log'), ('pic_log', 'pic log'), ('message_log', 'message log'),('stats', 'stats'), ('settings', 'settings')):
      if item[0] == current:
        out.append(item[1] + '&nbsp;')
      else:
        out.append('<a href="%s">%s</a>&nbsp;' % item)
    if add_after != None:
      out.append(add_after)
    #out.append('<br /><br /></div><br /><br />')
    out.append('<br /><br />')
    return out

  def die(self, message=''):
    self.origin.log("{0}:{1} wants to fuck around: {2}".format(self.client_address[0], self.client_address[1], message), 1)
    if self.origin.reject_debug:
      self.send_error('don\'t fuck around here mkay\n{0}'.format(message))
    else:
      self.send_error('don\'t fuck around here mkay')

  def __get_message_id_by_hash(self, hash):
    return self.origin.sqlite_hasher.execute("SELECT message_id FROM article_hashes WHERE message_id_hash = ?", (hash,)).fetchone()[0]
  
  def __breakit(self, rematch):
    return '%s ' % rematch.group(0)

  def handle_moderation_request(self):
    author = 'Anonymous'
    email = 'an@onymo.us'
    sender = '%s <%s>' % (author, email)
    now = int(time.time())
    subject = 'no subject'
    newsgroups = 'ctl'
    uid_rnd = ''.join(random.choice(string.ascii_lowercase) for x in range(10))
    uid_message_id = '<%s%i@%s>' % (uid_rnd, now, self.origin.uid_host)
    now = datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S +0000')
    lines = list()
    
    post_vars = FieldStorage(
      fp=self.rfile,
      headers=self.headers,
      environ={
        'REQUEST_METHOD':'POST',
        'CONTENT_TYPE':self.headers['Content-Type'],
      }
    )
    
    if 'secret' not in post_vars:
      self.die('%s not in post_vars' % item)
      return
    secret = post_vars['secret'].value
    if len(secret) != 64:
      self.die('secret has wrong length: %i instead of %i' % (len(secret), 64))
      return
    try:
      keypair = nacl.signing.SigningKey(unhexlify(secret))
    except Exception as e:
      self.die(e)
    for key in ('purge', 'purge_root'):
      if key in post_vars:
        purges = post_vars.getlist(key)
        for item in purges:
          try:
            lines.append("delete %s" % self.__get_message_id_by_hash(item))
          except Exception as e:
            self.origin.log("could not find message_id for hash %s: %s" % (item, e), 2)
    if 'delete_a' in post_vars:
      delete_attachments = post_vars.getlist('delete_a')
      for item in delete_attachments:
        try:
          lines.append("overchan-delete-attachment %s" % self.__get_message_id_by_hash(item))
        except Exception as e:
          self.origin.log("could not find message_d for hash %s: %s" % (item, e), 2)
    if len(lines) == 0:
      self.die('nothing to do')
      return
    #lines.append("")
    article = self.origin.template_message_control_inner.format(sender, now, newsgroups, subject, uid_message_id, self.origin.uid_host, "\n".join(lines))
    print "'%s'" % article
    hasher = sha512()
    old_line = None
    for line in article.split("\n")[:-1]:
      if old_line:
        hasher.update(old_line)
      old_line = '%s\r\n' % line
    hasher.update(old_line.replace("\r\n", ""))
    keypair = nacl.signing.SigningKey(unhexlify(secret))
    signature = hexlify(keypair.sign(hasher.digest()).signature)
    pubkey = hexlify(keypair.verify_key.encode())
    signed = self.origin.template_message_control_outer.format(sender, now, newsgroups, subject, uid_message_id, self.origin.uid_host, pubkey, signature, article)
    del keypair
    del signature
    del hasher
    f = open(os.path.join('incoming', 'tmp', uid_message_id), 'w')
    f.write(signed)
    f.close()
    del lines
    del article
    del signed
    os.rename(os.path.join('incoming', 'tmp', uid_message_id), os.path.join('incoming', uid_message_id))
    if 'target' in post_vars:
      target = post_vars['target'].value
    else:
      target = '/'
    self.send_redirect(target, 'moderation request received. will redirect you in a moment.', 2)

  def log_request(self, code):
    return

  def log_message(self, format):
    return

  def send_something(self, something):
    try:
      self.send_response(200)
      self.send_header('Content-type', 'text/html')
      self.end_headers()
      self.wfile.write('<html><head><title>foobar</title></head><body style="font-family: arial,helvetica,sans-serif; font-size: 10pt;"><center><br />your message has been received.<br />%s</center></body></html>' % something)
    except socket.error as e:
      if e.errno == 32:
        self.origin.log(e, 2)
        # Broken pipe
        pass
      else:
        raise e

class censor_httpd(threading.Thread):

  def __init__(self, thread_name, args):
    threading.Thread.__init__(self)
    self.name = thread_name
    if 'debug' not in args:
      self.debug = 2
      self.log('debuglevel not defined, using default of debug = 2', 2)
    else:
      try:
        self.debug = int(args['debug'])
        if self.debug < 0 or self.debug > 5:
          self.debug = 2
          self.log('debuglevel not between 0 and 5, using default of debug = 2', 2)
        else:
          self.log('using debuglevel {0}'.format(self.debug), 3)
      except ValueError as e:
        self.debug = 2
        self.log('debuglevel not between 0 and 5, using default of debug = 2', 2)
    self.log('initializing as plugin..', 2)
    self.should_terminate = False
    for key in ('bind_ip', 'bind_port', 'template_directory', 'censor', 'uid_host'):
      if not key in args:
        self.log('{0} not in args'.format(key), 0)
        self.should_terminate = True
    if self.should_terminate:
      self.log('terminating..'.format(key), 0)
      return
    self.uid_host = args['uid_host']
    self.ip = args['bind_ip']
    try:
      self.port = int(args['bind_port'])
    except ValueError as e:
      self.log("{0} is not a valid bind_port", 0)
      self.should_terminate = True
      self.log('terminating..'.format(key), 0)
      return
    if 'bind_use_ipv6' in args:
      tmp = args['bind_use_ipv6']
      if tmp.lower() == 'true':
        self.ipv6 = True
      elif tmp.lower() == 'false':
        self.ipv6 = False
      else:
        self.log("{0} is not a valid value for bind_use_ipv6. only true and false allowed.", 0)
        self.should_terminate = True
        self.log('terminating..'.format(key), 0)
        return
    #self.censor = args['censor']

    self.log('initializing httpserver..', 3)
    self.httpd = HTTPServer((self.ip, self.port), censor)
    if 'reject_debug' in args:
      tmp = args['reject_debug']
      if tmp.lower() == 'true':
        self.httpd.reject_debug = True
      elif tmp.lower() == 'false':
        self.httpd.reject_debug = False
      else:
        self.log("{0} is not a valid value for reject_debug. only true and false allowed. setting value to false.", 0)
    self.httpd.log = self.log
    self.httpd.rnd = open("/dev/urandom", "r")
    self.httpd.sessions = dict()
    self.httpd.uid_host = self.uid_host
    self.httpd.censor = args['censor']
    self.httpd.weekdays =  ('Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday')
    self.httpd.breaker = re.compile('([^ ]{16})')

    # read templates
    self.log('reading templates..', 3)
    template_directory = args['template_directory']
    f = open(os.path.join(template_directory, 'log.tmpl'), 'r')
    self.httpd.template_log = f.read()
    f.close()
    f = open(os.path.join(template_directory, 'log_accepted.tmpl'), 'r')
    self.httpd.template_log_accepted = f.read()
    f.close()
    f = open(os.path.join(template_directory, 'log_flagnames.tmpl'), 'r')
    self.httpd.template_log_flagnames = f.read()
    f.close()
    f = open(os.path.join(template_directory, 'log_flagset.tmpl'), 'r')
    self.httpd.template_log_flagset = f.read()
    f.close()
    f = open(os.path.join(template_directory, 'log_ignored.tmpl'), 'r')
    self.httpd.template_log_ignored = f.read()
    f.close()
    f = open(os.path.join(template_directory, 'log_unknown.tmpl'), 'r')
    self.httpd.template_log_unknown = f.read()
    f.close()
    f = open(os.path.join(template_directory, 'log_whitelist.tmpl'), 'r')
    self.httpd.template_log_whitelist = f.read()
    f.close()
    f = open(os.path.join(template_directory, 'message_control_inner.tmpl'), 'r')
    self.httpd.template_message_control_inner = f.read()
    f.close()
    f = open(os.path.join(template_directory, 'message_control_outer.tmpl'), 'r')
    self.httpd.template_message_control_outer = f.read()
    f.close()
    f = open(os.path.join(template_directory, 'modify_key.tmpl'), 'r')
    self.httpd.template_modify_key = f.read()
    f.close()
    f = open(os.path.join(template_directory, 'modify_key_flagset.tmpl'), 'r')
    self.httpd.template_modify_key_flagset = f.read()
    f.close()
    #f = open(os.path.join(template_directory, 'message_pic.template'), 'r')
    #self.httpd.template_message_pic = f.read()
    #f.close()
    #f = open(os.path.join(template_directory, 'message_signed.template'), 'r')
    #self.httpd.template_message_signed = f.read()
    #f.close()

  def shutdown(self):
    self.httpd.shutdown()

  def add_article(self, message_id, source="article"):
    self.log('this plugin does not handle any article. remove hook parts from {0}'.format(os.path.join('config', 'plugins', self.name.split('-', 1)[1])), 0)

  def run(self):
    if self.should_terminate:
      return
    # connect to hasher database
    # FIXME: add database_directory to postman?
    self.database_directory = ''
    self.httpd.sqlite_hasher_conn = sqlite3.connect('hashes.db3')
    self.httpd.sqlite_hasher = self.httpd.sqlite_hasher_conn.cursor()
    self.httpd.sqlite_censor_conn = sqlite3.connect('censor.db3')
    self.httpd.sqlite_censor = self.httpd.sqlite_censor_conn.cursor()
    # FIXME get overchan db path via arg
    self.httpd.sqlite_overchan_conn = sqlite3.connect('plugins/overchan/overchan.db3')
    self.httpd.sqlite_overchan = self.httpd.sqlite_overchan_conn.cursor()
    self.log('start listening at http://{0}:{1}'.format(self.ip, self.port), 1)
    self.httpd.serve_forever()
    self.httpd.sqlite_hasher_conn.close()
    self.httpd.sqlite_censor_conn.close()
    self.httpd.sqlite_overchan_conn.close()
    self.httpd.rnd.close()
    self.log('bye', 2)

  def log(self, message, debuglevel):
    if self.debug >= debuglevel:
      for line in str(message).split('\n'):
        print "[{0}] {1}".format(self.name, line)

if __name__ == '__main__':
  print "[%s] %s" % ("censor", "this plugin can't run as standalone version. yet.")
  exit(1)
