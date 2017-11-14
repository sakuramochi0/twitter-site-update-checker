#!/usr/bin/env python3
import datetime
import time
import os
import sys
import re
import argparse
import yaml
from urllib.parse import quote_plus

from dateutil.parser import parse
from get_mongo_client import get_mongo_client
import tweepy
from bs4 import BeautifulSoup
import requests

from get_tweepy import get_api

TCO_LINK_LEN = 23               # 'https://t.co/xxxxxxxxxx'の文字数

def main(args):
    # Run command
    if args.command == 'save_all_pages':
        save_all_pages()
    elif args.command == 'save_new_page':
        save_new_page()
    elif args.command == 'tweet_new_docs':
        tweet_new_docs()

def get_config(target):
    with open('config.yaml') as f:
        config = yaml.load(f)
    return config.get(target)
    
def save_all_pages():
    # Add the top page
    top_url = os.path.join(config['base_url'], config['top_url'])
    urls = [top_url]
    # Append backnumber pages
    soup = get_soup(top_url)
    back_urls = [os.path.join(config['base_url'], a['href'])
                 for a in soup.select('.bacnnum_link a')]
    urls.extend(back_urls)
    for url in urls:
        save_page(url)

def save_new_page():
    top_url = os.path.join(config['base_url'], config['top_url'])
    save_page(top_url)

def save_page(url):
    soup = get_soup(url)
    entries = parse_page(soup)
    docs = map(parse_entry, entries)
    ids = insert_docs(docs)

def insert_docs(docs):
    ids = []
    c = get_mongo_client()[config['target']].entries
    for doc in docs:
        if c.find({'_id': doc['_id']}).count():
            continue
        res = c.insert_one(doc)
        ids.append(res.inserted_id)
    return ids
    
def parse_page(soup):
    """Parse page for prismstone. (not abstructed)"""
    if config['target'] == 'prismstone_newitem':
        entries = soup.select('div.info_entry_inbox div')
    elif config['target'] == 'prismstone_shoplist':
        entries = soup.select('table.shoptable th')
    else:
        entries = soup.select('div.info_entry')
    return entries

def parse_entry(entry):
    """Parse entry for prismstone. TODO: abstruct for general shop"""
    # ニューアイテムの場合
    if config['target'] == 'prismstone_newitem':
        imgs = [os.path.join(config['base_url'], img['src'])
                for img in entry.find_all('img')]

        _id = os.path.splitext(os.path.basename(imgs[0]))[0]
        today = datetime.date.today()
        date = parse(today.ctime())
        header = '{date}に追加されたニューアイテム'.format(date=format_date(today))
        body = ''
        doc = dict(
            _id = _id,
            meta = {'tweeted': False},
            date = date,
            header = header,
            body = body,
            imgs = imgs,
        )
    # ショップリストの場合
    elif config['target'] == 'prismstone_shoplist':
        name = entry.text.strip()

        # Get shop imgs and address
        tr = entry.parent.find_next_sibling()
        imgs = []
        for td in tr.select('td'):
            if td.select('img'):
                imgs = [os.path.join(os.path.dirname(config['base_url']), img['src']) for img in tr.select('img')]
                continue
            else:
                address = td.text.strip().replace('\r', '')
                break

        _id = name
        map_base_url = 'https://www.google.com/maps/search/'
        map_url = os.path.join(map_base_url, quote_plus(address.split()[0]))
        header = '「{name}」が追加されました。\n地図：{map_url}'.format(
            name = name,
            address = address,
            map_url = map_url,
        )
        body = ''
        date = parse(datetime.date.today().ctime())
        doc = dict(
            _id = _id,
            meta = {'tweeted': False},
            date = date,
            header = header,
            body = body,
            imgs = imgs,
        )
    # その他の場合
    else:
        _id = entry['id']
        body = entry.find(class_='info_entry_inbox')
        date = re.search(r'\d{4}/\d{1,2}/\d{1,2}', entry.h2.text).group(0)
        date = parse(date)
        header = entry.find('strong').text.replace('\r', '')
        body_text = body.text.replace('\r', '').strip()
        imgs = [os.path.join(config['base_url'], img['src'])
                for img in body.find_all('img')]
        doc = dict(
            _id = _id,
            meta = {'tweeted': False},
            date = date,
            header = header,
            body = body_text,
            imgs = imgs,
        )
    return doc

def format_date(date):
    WEEKDAY = '月火水木金土日'
    return '{d.year}年{d.month}月{d.day}日({wday})'.format(
        d=date, wday=WEEKDAY[date.weekday()])

def tweet_new_docs():
    c = get_mongo_client()[config['target']].entries
    docs = c.find({'meta.tweeted': False}).sort('_id')
    for doc in docs:
        success_id = tweet_doc(doc)
        c.update_one({'_id': success_id},
                     {'$set': {'meta.tweeted': True}})

def tweet_doc(doc):
    status = make_status(doc)
    success = tweet(status, imgs=doc['imgs'][:4])
    if success:
        return doc['_id']
    else:
        return False

def make_status(doc):
    url = os.path.join(config['base_url'], config['top_url'])
    tweet_template = config['tweet_template'].replace(r'\n', '\n')
    status = tweet_template.format(
        date = doc['date'].strftime('%Y/%m/%d'),
        header = doc['header'].replace(r'\n', '\n'),
        body = '{body}',
        url = '{url}',
    )
    # '{body}'の6文字を消して最後に'…'を追加するから5文字余裕がある
    # 
    max_body_len = 140 - len(status) + 5 - TCO_LINK_LEN + 5
    
    # Modify body text
    body = doc['body'].replace('\n', '')[:max_body_len] + '…'
    status = status.format(body=body, url=url)
    return status

def tweet(status, imgs=None):
    print('-' * 8)
    print(status)
    print('imgs({}):'.format(len(imgs)), imgs)
    # Prepare madia_ids
    media_ids = []
    if imgs:
        for img in imgs:
            filename = download_image(img)
            if filename:
                media_id = api.media_upload(filename).media_id
                media_ids.append(media_id)

    # Do actual tweet
    api.update_status(status=status, media_ids=media_ids)
    return True
    
def download_image(img):
    r = requests.get(img)
    if not r.ok:
        return False
    filename = os.path.join('/tmp', os.path.basename(img))
    with open(filename, 'wb') as f:
        f.write(r.content)
    return filename
    
def get_soup(url, encoding='utf-8'):
    r = requests.get(url)
    r.encoding = encoding
    return BeautifulSoup(r.text, 'lxml')

if __name__ == '__main__':
    # Parse command line args
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', '-d', action='store_true')
    parser.add_argument('target')
    parser.add_argument('command', choices=[
        'save_all_pages',
        'save_new_page',
        'tweet_new_docs',
    ])
    args = parser.parse_args()

    # Load config
    config = get_config(args.target)
    if not config:
        sys.exit(1)

    # Get twitter api
    if args.debug:
        api = get_api(config['debug_account'])
    else:
        api = get_api(config['account'])
    
    main(args)
