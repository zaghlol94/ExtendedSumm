import gc
import glob
import hashlib
import json
import os
import pickle
import re
import subprocess
import xml.etree.ElementTree as ET
from os.path import join as pjoin

import pandas as pd
import torch
from multiprocess import Pool
from tqdm import tqdm

from others.logging import logger
from others.tokenization import BertTokenizer
from others.utils import clean
from prepro.utils import _get_word_ngrams

nyt_remove_words = ["photo", "graph", "chart", "map", "table", "drawing"]


def recover_from_corenlp(s):
    s = re.sub(r' \'{\w}', '\'\g<1>', s)
    s = re.sub(r'\'\' {\w}', '\'\'\g<1>', s)


def load_json(src_json, lower):
    source = []
    tgt = []
    # flag = False
    id = json.load(open(src_json))['docId']
    for sent in json.load(open(src_json))['sentences']:
        tokens = [t['word'] for t in sent['tokens']]
        if (lower):
            tokens = [t.lower() for t in tokens]
        source.append(tokens)

    for sent in json.load(open(src_json.replace('src', 'tgt')))['sentences']:
        tokens = [t['word'] for t in sent['tokens']]
        if (lower):
            tokens = [t.lower() for t in tokens]
        tgt.append(tokens)

    source = [clean(' '.join(sent)).split() for sent in source]
    tgt = [clean(' '.join(sent)).split() for sent in tgt]
    return source, tgt, id


def load_xml(p):
    tree = ET.parse(p)
    root = tree.getroot()
    title, byline, abs, paras = [], [], [], []
    title_node = list(root.iter('hedline'))
    if (len(title_node) > 0):
        try:
            title = [p.text.lower().split() for p in list(title_node[0].iter('hl1'))][0]
        except:
            print(p)

    else:
        return None, None
    byline_node = list(root.iter('byline'))
    byline_node = [n for n in byline_node if n.attrib['class'] == 'normalized_byline']
    if (len(byline_node) > 0):
        byline = byline_node[0].text.lower().split()
    abs_node = list(root.iter('abstract'))
    if (len(abs_node) > 0):
        try:
            abs = [p.text.lower().split() for p in list(abs_node[0].iter('p'))][0]
        except:
            print(p)

    else:
        return None, None
    abs = ' '.join(abs).split(';')
    abs[-1] = abs[-1].replace('(m)', '')
    abs[-1] = abs[-1].replace('(s)', '')

    for ww in nyt_remove_words:
        abs[-1] = abs[-1].replace('(' + ww + ')', '')
    abs = [p.split() for p in abs]
    abs = [p for p in abs if len(p) > 2]

    for doc_node in root.iter('block'):
        att = doc_node.get('class')
        # if(att == 'abstract'):
        #     abs = [p.text for p in list(f.iter('p'))]
        if (att == 'full_text'):
            paras = [p.text.lower().split() for p in list(doc_node.iter('p'))]
            break
    if (len(paras) > 0):
        if (len(byline) > 0):
            paras = [title + ['[unused3]'] + byline + ['[unused4]']] + paras
        else:
            paras = [title + ['[unused3]']] + paras

        return paras, abs
    else:
        return None, None


def sent_sect_arxiv(args):
    corpura = ['train', 'val', 'test']
    files = []
    json_dir = '/disk1/sajad/datasets/download_google_drive/arxiv/json/'
    for corpus_type in corpura:
        papers = {}
        files = []
        for f in glob.glob('/disk1/sajad/datasets/download_google_drive/arxiv/inputs/' + corpus_type + '/*.json'):
            files.append(f)
        corpora = {corpus_type: files}
        for corpus_type in corpora.keys():
            a_lst = [(f, corpus_type, args) for f in corpora[corpus_type]]
            papers = {}
            # for a in a_lst:
            #     data = _sent_sect_arxiv(a)
            # papers[data['paper_id']] = data['sent_sect_labels']

            pool = Pool(12)
            for data in tqdm(pool.imap_unordered(_sent_sect_arxiv, a_lst), total=len(a_lst)):
                papers[data['paper_id']] = data['sent_sect_labels']

            pool.close()
            pool.join()

            with open(json_dir + corpus_type + '-sect' + '.pkl', 'wb') as handle:
                pickle.dump(papers, handle, protocol=pickle.HIGHEST_PROTOCOL)


def _sent_sect_arxiv(params):
    f, set, args = params
    test_kws = pd.read_csv('cspubsum_titles.csv')

    kws = {
        'intro': [kw.strip() for kw in test_kws['intro'].dropna()],
        'related': [kw.strip() for kw in test_kws['related work'].dropna()],
        'experiments': [kw.strip() for kw in test_kws['experiments'].dropna()],
        'results': [kw.strip() for kw in test_kws['results'].dropna()],
        'conclusion': [kw.strip() for kw in test_kws['conclusion'].dropna()]
    }

    papers = {}
    with open(f, 'r') as f:
        paper = json.loads(f.read())
        paper_id = paper['id']
        paper_sect_labels = []
        for i, sect in enumerate(paper['section_names']):
            sect_main_title = sect.lower().strip()
            sentence_num = paper['section_lengths'][i]
            if len(sect_main_title.strip()) > 0:
                if 'introduction' in sect_main_title.split()[0] or sect_main_title in kws['intro']:
                    paper_sect_labels.extend([0] * sentence_num)

                elif sect_main_title in kws['related']:
                    paper_sect_labels.extend([1] * sentence_num)

                elif sect_main_title in kws['experiments']:
                    paper_sect_labels.extend([3] * sentence_num)

                elif 'result' in sect_main_title \
                        or 'discussion' in sect_main_title \
                        or sect_main_title in kws['results']:
                    paper_sect_labels.extend([4] * sentence_num)

                elif 'conclusion' in sect_main_title or 'summary' in sect_main_title or sect_main_title in kws[
                    'conclusion']:
                    paper_sect_labels.extend([5] * sentence_num)

                else:
                    paper_sect_labels.extend([2] * sentence_num)

        if 5 in paper_sect_labels:
            paper_sect_labels = paper_sect_labels[:len(paper_sect_labels) - paper_sect_labels[::-1].index(5)]
        elif 4 in paper_sect_labels:
            paper_sect_labels = paper_sect_labels[:len(paper_sect_labels) - paper_sect_labels[::-1].index(4)]

        return {'paper_id': paper_id, 'sent_sect_labels': paper_sect_labels}


def sent_sect_mine(args):
    json_dirs = []
    # for c_type in ['train', 'val', 'test']:
    for c_type in ['val']:
        json_dirs.append(args.raw_path + c_type + '-new5.json')
        # _sent_sect(args.raw_path + c_type + '.json')

    for j in json_dirs:
        _sent_sect_mine(j)


def hashhex(s):
    """Returns a heximal formated SHA1 hash of the input string."""
    h = hashlib.sha1()
    h.update(s.encode('utf-8'))
    return h.hexdigest()

def _sent_sect_mine(json_dir):
    global sentence_num
    test_kws = pd.read_csv('cspubsum_title_4.csv')

    kws = {
        'intro': [kw.strip() for kw in test_kws['intro'].dropna()],
        'related': [kw.strip() for kw in test_kws['related work'].dropna()],
        # 'experiments': [kw.strip() for kw in test_kws['experiments'].dropna()],
        # 'results': [kw.strip() for kw in test_kws['results'].dropna()],
        'discussion': [kw.strip() for kw in test_kws['discussion'].dropna()]
    }

    papers = {}
    print(f'Reading {json_dir}')
    titles = []
    dup_titles = []
    line_num = sum(1 for line in open(json_dir, 'r'))
    with open(json_dir) as f:
        for line in tqdm(f, total=line_num):
            try:
                total_sent = 0
                paper = json.loads(line)

                # if paper['title'] not in titles:
                #     titles.append(paper['title'])
                # else:
                #     dup_titles.append(paper['title'])

                paper_id = hashhex(paper["title"].lower().strip())

                papers[paper_id] = []
                for sect in paper["body"]:
                    sect_body = sect['section_body']

                    if len(sect['sub']) > 0:
                        for s in sect['sub']:
                            sect_body.extend(s['section_body'])


                    sentence_num = len(sect_body)
                    total_sent += sentence_num



                    sect_main_title = sect['section_title'].lower().strip()
                    if 'introduction' in sect_main_title.split()[0] or sect_main_title in kws['intro']:
                        papers[paper_id].extend([0] * sentence_num)

                    elif sect_main_title in kws['related']:
                        papers[paper_id].extend([1] * sentence_num)

                    elif sect_main_title in kws['discussion']:
                        papers[paper_id].extend([3] * sentence_num)

                    # elif 'result' in sect_main_title \
                    #         or 'discussion' in sect_main_title \
                    #         or sect_main_title in kws['results']:
                    #     papers[paper_id].extend([4] * sentence_num)
                    #
                    # elif 'conclusion' in sect_main_title or 'summary' in sect_main_title or sect_main_title in kws[
                    #     'conclusion']:
                    #     papers[paper_id].extend([5] * sentence_num)

                    else:
                        # Methodology
                        papers[paper_id].extend([2] * sentence_num)
            except:
                del papers[paper_id]
                continue
        print(len(papers))
        # assert len(papers[paper_id]) == total_sent, "Mismtch between sentence num"
    with open(json_dir.replace('.json', '') + '-sect-4' + '.pkl', 'wb') as handle:
        pickle.dump(papers, handle, protocol=pickle.HIGHEST_PROTOCOL)


def tokenize(args):
    src_dir = os.path.abspath(args.raw_path + '/src/')
    tokenized_src_dir = os.path.abspath(args.save_path + '/tokenized/src/')

    tgt_dir = os.path.abspath(args.raw_path + '/tgt/')
    tokenized_tgt_dir = os.path.abspath(args.save_path + '/tokenized/tgt/')

    print("Preparing to tokenize %s to %s..." % (src_dir, tokenized_src_dir))
    srcs = os.listdir(src_dir)
    tgts = os.listdir(tgt_dir)
    # make IO list file

    print("Making list of files to tokenize...")
    with open("mapping_for_corenlp.txt", "w") as f:
        for s in srcs:
            # if (not s.endswith('story')):
            #     continue
            f.write("%s\n" % (os.path.join(src_dir, s)))
    command = ['java', 'edu.stanford.nlp.pipeline.StanfordCoreNLP', '-annotators', 'tokenize,ssplit',
               '-ssplit.newlineIsSentenceBreak', 'always', '-filelist', 'mapping_for_corenlp.txt', '-outputFormat',
               'json', '-outputDirectory', tokenized_src_dir]
    print("Tokenizing %i files in %s and saving in %s..." % (len(srcs), src_dir, tokenized_src_dir))
    subprocess.call(command)
    print("Stanford CoreNLP Tokenizer has finished.")
    os.remove("mapping_for_corenlp.txt")

    print("Making list of files to tokenize...")
    with open("mapping_for_corenlp.txt", "w") as f:
        for t in tgts:
            # if (not s.endswith('story')):
            #     continue
            f.write("%s\n" % (os.path.join(tgt_dir, t)))
    command = ['java', 'edu.stanford.nlp.pipeline.StanfordCoreNLP', '-annotators', 'tokenize,ssplit',
               '-ssplit.newlineIsSentenceBreak', 'always', '-filelist', 'mapping_for_corenlp.txt', '-outputFormat',
               'json', '-outputDirectory', tokenized_tgt_dir]
    print("Tokenizing %i files in %s and saving in %s..." % (len(tgts), tgt_dir, tokenized_tgt_dir))
    subprocess.call(command)
    print("Stanford CoreNLP Tokenizer has finished.")
    os.remove("mapping_for_corenlp.txt")

    # Check that the tokenized srcs directory contains the same number of files as the original directory
    num_orig = len(os.listdir(src_dir))
    num_tokenized = len(os.listdir(tokenized_src_dir))
    # import pdb;pdb.set_trace()
    if num_orig != num_tokenized:
        raise Exception(
            "The tokenized srcs directory %s contains %i files, but it should contain the same number as %s (which has %i files). Was there an error during tokenization?" % (
                tokenized_src_dir, num_tokenized, src_dir, num_orig))
    print("Successfully finished tokenizing %s to %s.\n" % (src_dir, tokenized_src_dir))


def cal_rouge(evaluated_ngrams, reference_ngrams):
    reference_count = len(reference_ngrams)
    evaluated_count = len(evaluated_ngrams)

    overlapping_ngrams = evaluated_ngrams.intersection(reference_ngrams)
    overlapping_count = len(overlapping_ngrams)

    if evaluated_count == 0:
        precision = 0.0
    else:
        precision = overlapping_count / evaluated_count

    if reference_count == 0:
        recall = 0.0
    else:
        recall = overlapping_count / reference_count

    f1_score = 2.0 * ((precision * recall) / (precision + recall + 1e-8))
    return {"f": f1_score, "p": precision, "r": recall}


def identify_sent_sects(paper_sent_sect, segment_sent_ids, lst_segment=False):
    """
    :param sent:
    :type sent:
    :return:
        6 labels:
            "intro": 0
            "related": 1
            "methodology": 2
            "experimental": 3
            "results" : 4
            "conclusion": 5
    :rtype:
    """

    sects = []
    for id in segment_sent_ids:
        try:
            sects.append(paper_sent_sect[id])
        except:
            try:
                lst = paper_sent_sect[-1]
                sects.append(lst)
            except:
                pass
                # import pdb;
                # pdb.set_trace()
    # import pdb;pdb.set_trace()
    assert len(segment_sent_ids) == len(
        sects), "Number of sents in segment should be the same with sect labels assigned to"
    return sects


def greedy_selection(doc_sent_list, abstract_sent_list, summary_size):
    def _rouge_clean(s):
        return re.sub(r'[^a-zA-Z0-9 ]', '', s)

    max_rouge = 0.0
    abstract = sum(abstract_sent_list, [])
    abstract = _rouge_clean(' '.join(abstract)).split()
    sents = [_rouge_clean(' '.join(s)).split() for s in doc_sent_list]
    evaluated_1grams = [_get_word_ngrams(1, [sent]) for sent in sents]
    reference_1grams = _get_word_ngrams(1, [abstract])
    evaluated_2grams = [_get_word_ngrams(2, [sent]) for sent in sents]
    reference_2grams = _get_word_ngrams(2, [abstract])

    selected = []
    for s in range(summary_size):
        cur_max_rouge = max_rouge
        cur_id = -1
        for i in range(len(sents)):
            if (i in selected):
                continue
            c = selected + [i]
            candidates_1 = [evaluated_1grams[idx] for idx in c]
            candidates_1 = set.union(*map(set, candidates_1))
            candidates_2 = [evaluated_2grams[idx] for idx in c]
            candidates_2 = set.union(*map(set, candidates_2))
            rouge_1 = cal_rouge(candidates_1, reference_1grams)['f']
            rouge_2 = cal_rouge(candidates_2, reference_2grams)['f']
            rouge_score = rouge_1 + rouge_2
            if rouge_score > cur_max_rouge:
                cur_max_rouge = rouge_score
                cur_id = i
        if (cur_id == -1):
            return selected
        selected.append(cur_id)
        max_rouge = cur_max_rouge

    return sorted(selected)



    """Returns a heximal formated SHA1 hash of the input string."""
    h = hashlib.sha1()
    h.update(s.encode('utf-8'))
    return h.hexdigest()


def tokenize_with_corenlp(input_text, source_folder, out_folder, id, options='tokenize,ssplit', section_title=''):
    in_file = open(source_folder + str(id) + '-' + section_title + '.txt', mode='w')
    in_file.write(input_text.strip())
    in_file.close()

    command = ['java', 'edu.stanford.nlp.pipeline.StanfordCoreNLP', '-annotators', options,
               '-ssplit.newlineIsSentenceBreak', 'always', '-file',
               source_folder + str(id) + '-' + section_title + '.txt', '-outputFormat',
               'json', '-outputDirectory', out_folder]
    subprocess.call(command)
    with open(out_folder + str(id) + '-' + section_title + '.txt.json') as f:
        tokenized = json.loads(f.read())

    out_tokenized = []

    for sent in tokenized['sentences']:
        sent_tokens = []
        for i, tkn in enumerate(sent['tokens']):
            sent_tokens.append(tkn['word'])
        out_tokenized.append(sent_tokens.copy())

    # os.remove('input.txt')
    # os.remove('tokenized/input.txt.json')

    return out_tokenized


class BertData():
    def __init__(self, args):
        self.args = args
        self.tokenizer = BertTokenizer.from_pretrained('bert-base-uncased', do_lower_case=True)

        self.sep_token = '[SEP]'
        self.cls_token = '[CLS]'
        self.pad_token = '[PAD]'
        self.tgt_bos = '[unused0]'
        self.tgt_eos = '[unused1]'
        self.tgt_sent_split = '[unused2]'
        self.sep_vid = self.tokenizer.vocab[self.sep_token]
        self.cls_vid = self.tokenizer.vocab[self.cls_token]
        self.pad_vid = self.tokenizer.vocab[self.pad_token]

    def preprocess(self, src, tgt, sent_labels, use_bert_basic_tokenizer=False, is_test=False):

        if ((not is_test) and len(src) == 0):
            return None

        original_src_txt = [' '.join(s) for s in src]

        idxs = [i for i, s in enumerate(src)]

        _sent_labels = [0] * len(src)
        for l in sent_labels:
            _sent_labels[l] = 1

        src = [src[i] for i in idxs]
        sent_labels = [_sent_labels[i] for i in idxs]
        src = src[:self.args.max_src_nsents]
        sent_labels = sent_labels

        if ((not is_test) and len(src) < self.args.min_src_nsents):
            return None

        src_txt = [' '.join(sent) for sent in src]
        text = ' {} {} '.format(self.sep_token, self.cls_token).join(src_txt)

        src_subtokens = self.tokenizer.tokenize(text)

        src_subtokens = [self.cls_token] + src_subtokens + [self.sep_token]
        src_subtoken_idxs = self.tokenizer.convert_tokens_to_ids(src_subtokens)
        _segs = [-1] + [i for i, t in enumerate(src_subtoken_idxs) if t == self.sep_vid]
        segs = [_segs[i] - _segs[i - 1] for i in range(1, len(_segs))]
        segments_ids = []
        for i, s in enumerate(segs):
            if (i % 2 == 0):
                segments_ids += s * [0]
            else:
                segments_ids += s * [1]
        cls_ids = [i for i, t in enumerate(src_subtoken_idxs) if t == self.cls_vid]
        sent_labels = sent_labels[:len(cls_ids)]

        tgt_subtokens_str = '[unused0] ' + ' [unused2] '.join(
            [' '.join(self.tokenizer.tokenize(' '.join(tt), use_bert_basic_tokenizer=use_bert_basic_tokenizer)) for tt
             in tgt]) + ' [unused1]'
        tgt_subtoken = tgt_subtokens_str.split()[:self.args.max_tgt_ntokens]
        if ((not is_test) and len(tgt_subtoken) < self.args.min_tgt_ntokens):
            return None

        tgt_subtoken_idxs = self.tokenizer.convert_tokens_to_ids(tgt_subtoken)

        tgt_txt = '<q>'.join([' '.join(tt) for tt in tgt])
        src_txt = [original_src_txt[i] for i in idxs]

        return src_subtoken_idxs, sent_labels, tgt_subtoken_idxs, segments_ids, cls_ids, src_txt, tgt_txt


def format_to_bert(args):
    if (args.dataset != ''):
        datasets = [args.dataset]
    else:
        datasets = ['test']

    for corpus_type in datasets:
        with open(args.sect_label_path + corpus_type + '-new-sect-4.pkl',
                  'rb') as handle:
            sent_sect = pickle.load(handle)
        a_lst = []


        for json_f in glob.glob(pjoin(args.raw_path, 'json', '*' + corpus_type + '.*.json')):
            real_name = json_f.split('/')[-1]
            a_lst.append(
                (corpus_type, json_f, args, pjoin(args.save_path, real_name.replace('json', 'bert.pt')), sent_sect))
        for a in a_lst:
            _format_to_bert(a)
        # pool = Pool(15)
        # for d in pool.imap(_format_to_bert, a_lst):
        #     pass
        #
        # pool.close()
        # pool.join()


#

#

def format_to_bert_arxiv(args):
    if (args.dataset != ''):
        datasets = [args.dataset]
    else:
        datasets = ['test']
    for corpus_type in datasets:
        with open('/disk1/sajad/datasets/download_google_drive/arxiv/json/' + corpus_type + '-new-sect-4.pkl',
                  'rb') as handle:
            sent_sect = pickle.load(handle)
        a_lst = []
        c = 0

        for json_f in glob.glob(pjoin(args.raw_path, 'json/*' + corpus_type + '.*.json')):

            real_name = json_f.split('/')[-1]
            if not os.path.exists(pjoin(args.save_path, real_name.replace('json', 'bert.pt'))):
                c += 1
            a_lst.append(
                (corpus_type, json_f, args, pjoin(args.save_path, real_name.replace('json', 'bert.pt')), sent_sect))
        print("Number of files: " + str(c))

        # for a in a_lst:
        #     _format_to_bert(a)

        pool = Pool(20)
        for d in pool.imap(_format_to_bert, a_lst):
            pass

        pool.close()
        pool.join()


def _format_to_bert(param):
    corpus_type, json_file, args, save_file, sent_sect_dict = param
    emp = 0
    is_test = corpus_type == 'test'
    if (os.path.exists(save_file)):
        logger.info('Ignore %s' % save_file)
        return
    bert = BertData(args)
    logger.info('Processing %s' % json_file)
    print(f"Reading {json_file}")
    jobs = json.load(open(json_file))
    datasets = []


    for j, data in tqdm(enumerate(jobs), total=len(jobs)):
        # source, tgt, paper_id = data['tgt'], data['id'], data['src']
        source, tgt, paper_id = data['src'], data['tgt'], data['id']
        if len(source) > 1 and len(tgt) > 0 and len(paper_id) > 0:
            segment = []
            segment_sent_num = []
            token_ctr = 0
            i = 0

            # if len(sent_sect_dict[paper_id]) != len(source):
            #     print('Terribke1')
            #     import pdb;pdb.set_trace()
            try:
                while i < len(sent_sect_dict[paper_id]):
                    try:
                        sent = source[i]
                    except:
                        # import pdb;pdb.set_trace()
                        print('Sentence not found')
                        break
                    if len(sent) + token_ctr < 1024:
                        segment.append(sent)
                        segment_sent_num.append(i)
                        token_ctr += len(sent)
                        # print(i)
                        if i == len(sent_sect_dict[paper_id]) - 1:
                            token_ctr = 0
                            lst_segment = True
                            sent_labels = greedy_selection(segment, tgt, 3)
                            sent_sect_labels = identify_sent_sects(sent_sect_dict[paper_id], segment_sent_num.copy(),
                                                                   lst_segment)
                            if (args.lower):
                                segment = [' '.join(s).lower().split() for s in segment]
                                tgt = [' '.join(s).lower().split() for s in tgt]
                            b_data = bert.preprocess(segment.copy(), tgt, sent_labels,
                                                     use_bert_basic_tokenizer=args.use_bert_basic_tokenizer,
                                                     is_test=is_test)
                            if (b_data is None):
                                continue
                            src_subtoken_idxs, sent_labels, tgt_subtoken_idxs, segments_ids, cls_ids, src_txt, tgt_txt = b_data
                            try:
                                assert len(sent_sect_labels) == len(
                                    sent_labels), "Number of segment_sent and section_sents should be the same"
                                assert len(cls_ids) == len(
                                    sent_labels), "Number of segment_sent and section_sents should be the same"
                            except:
                                import pdb;
                                pdb.set_trace()

                            b_data_dict = {"src": src_subtoken_idxs, "tgt": tgt_subtoken_idxs,
                                           "src_sent_labels": sent_labels, "segs": segments_ids, 'clss': cls_ids,
                                           'src_txt': src_txt, "tgt_txt": tgt_txt, "paper_id": paper_id,
                                           "sent_sect_labels": sent_sect_labels}

                            datasets.append(b_data_dict.copy())
                            segment_sent_num.clear()
                            segment.clear()


                    else:
                        if len(sent) >= 1024 and len(segment) == 0:
                            # import pdb;pdb.set_trace()
                            sent_sect_dict[paper_id] = sent_sect_dict[paper_id][:i] + sent_sect_dict[paper_id][i + 1:]
                            source = source[:i] + source[i + 1:]
                            # i = i + 1
                            continue
                        # import pdb;pdb.set_trace()
                        i = i - 1
                        token_ctr = 0
                        sent_labels = greedy_selection(segment, tgt, 3)
                        sent_sect_labels = identify_sent_sects(sent_sect_dict[paper_id], segment_sent_num.copy())
                        if (args.lower):
                            segment = [' '.join(s).lower().split() for s in segment]
                            tgt = [' '.join(s).lower().split() for s in tgt]

                        b_data = bert.preprocess(segment.copy(), tgt, sent_labels,
                                                 use_bert_basic_tokenizer=args.use_bert_basic_tokenizer,
                                                 is_test=is_test)
                        # b_data = bert.preprocess(source, tgt, sent_labels, use_bert_basic_tokenizer=args.use_bert_basic_tokenizer)
                        if (b_data is None):
                            continue

                        src_subtoken_idxs, sent_labels, tgt_subtoken_idxs, segments_ids, cls_ids, src_txt, tgt_txt = b_data

                        assert len(sent_sect_labels) == len(
                            sent_labels), "Number of segment_sent and section_sents should be the same"
                        assert len(cls_ids) == len(
                            sent_labels), "Number of segment_sent and section_sents should be the same"
                        assert len(cls_ids) == len(
                            sent_sect_labels), "Number of segment_sent and section_sents should be the same"

                        b_data_dict = {"src": src_subtoken_idxs, "tgt": tgt_subtoken_idxs,
                                       "src_sent_labels": sent_labels, "segs": segments_ids, 'clss': cls_ids,
                                       'src_txt': src_txt, "tgt_txt": tgt_txt, "paper_id": paper_id,
                                       "sent_sect_labels": sent_sect_labels}
                        datasets.append(b_data_dict.copy())
                        segment_sent_num.clear()
                        segment.clear()

                    i += 1
            except:
                emp+=1
                continue
        else:
            emp += 1
            continue
    if emp > 0: print(f'Empty: {emp}')
    logger.info('Processed instances %d' % len(datasets))
    logger.info('Saving to %s' % save_file)
    torch.save(datasets, save_file)
    datasets = []
    gc.collect()




def format_to_lines_mine(args):
    if args.dataset != '':
        corpus_type = args.dataset
    else:
        corpus_type = 'train'
    corpus_mapping = {}
    file = []
    # import pdb;pdb.set_trace()
    for f in glob.glob(pjoin(args.raw_path.replace('files', ''), corpus_type +'-new5.json')):
        file.append(f)
    corpora = {corpus_type: file}
    dataset = []
    for corpus_type in corpora.keys():
        p_ct = 0
        n_lines = sum([1 for _ in open(file[0])])
        with open(file[0]) as f:
            for line in tqdm(f, total=n_lines):
                paper = json.loads(line)
                sect_body = []
                for sect in paper["body"]:
                    sect_body.extend(sect['section_body'])

                    if len(sect['sub']) > 0:
                        for s in sect['sub']:
                            sect_body.extend(s['section_body'])

                dataset.append({'id': hashhex(paper['title'].lower().strip()),
                                'src': sect_body,
                                'tgt': paper['paper_summary']})
                if (len(dataset) > args.shard_size):
                    pt_file = "{:s}{:s}.{:d}.json".format(args.save_path, corpus_type, p_ct)
                    with open(pt_file, 'w') as save:
                        # save.write('\n'.join(dataset))
                        save.write(json.dumps(dataset))
                        p_ct += 1
                        dataset = []
        if len(dataset) > 0:
            pt_file = "{:s}{:s}.{:d}.json".format(args.save_path, corpus_type, p_ct)
            with open(pt_file, 'w') as save:
                # save.write('\n'.join(dataset))
                save.write(json.dumps(dataset))
                p_ct += 1
                dataset = []


def format_to_lines(args):
    if args.dataset != '':
        corpus_type = args.dataset
    else:
        corpus_type = 'train'
    corpus_mapping = {}
    files = []
    # import pdb;pdb.set_trace()
    for f in glob.glob(pjoin(args.raw_path, corpus_type + '/tokenized/src/*.json')):
        files.append(f)
    corpora = {corpus_type: files}
    for corpus_type in corpora.keys():
        a_lst = [(f, args) for f in corpora[corpus_type]]
        pool = Pool(args.n_cpus)
        dataset = []
        p_ct = 0
        for d in tqdm(pool.imap_unordered(_format_to_lines, a_lst), total=len(a_lst)):
            dataset.append(d)
            # print(args.shard_size)
            if (len(dataset) > args.shard_size):
                pt_file = "{:s}{:s}.{:d}.json".format(args.save_path, corpus_type, p_ct)
                with open(pt_file, 'w') as save:
                    # save.write('\n'.join(dataset))
                    save.write(json.dumps(dataset))
                    p_ct += 1
                    dataset = []

        pool.close()
        pool.join()
        if (len(dataset) > 0):
            print(args.save_path)
            pt_file = "{:s}{:s}.{:d}.json".format(args.save_path, corpus_type, p_ct)
            print(pt_file)
            with open(pt_file, 'w') as save:
                # save.write('\n'.join(dataset))
                save.write(json.dumps(dataset))
                p_ct += 1
                dataset = []


def _format_to_lines(params):
    f, args = params
    print(f)
    source, tgt, id = load_json(f, args.lower)
    return {'id': id.replace('.txt', '').replace('.json', '').strip(), 'src': source, 'tgt': tgt}


def format_arxiv_to_lines(args):
    if args.dataset != '':
        corpus_type = args.dataset
    else:
        corpus_type = 'train'
    corpus_mapping = {}
    files = []
    for f in glob.glob('/disk1/sajad/datasets/download_google_drive/arxiv/inputs/' + corpus_type + '/*.json'):
        files.append(f)
    corpora = {corpus_type: files}
    for corpus_type in corpora.keys():
        a_lst = [(f, corpus_type, args) for f in corpora[corpus_type]]
        pool = Pool(args.n_cpus)
        dataset = []
        p_ct = 0
        # for a in a_lst:
        #     _format_arxiv_to_lines(a)
        for d in tqdm(pool.imap_unordered(_format_arxiv_to_lines, a_lst), total=len(a_lst)):
            dataset.append(d)
            # print(args.shard_size)
            if (len(dataset) > args.shard_size):
                pt_file = "{:s}{:s}.{:d}.json".format(args.save_path, corpus_type, p_ct)
                with open(pt_file, 'w') as save:
                    # save.write('\n'.join(dataset))
                    save.write(json.dumps(dataset))
                    p_ct += 1
                    dataset = []

        pool.close()
        pool.join()
        if (len(dataset) > 0):
            print(args.save_path)
            pt_file = "{:s}{:s}.{:d}.json".format(args.save_path, corpus_type, p_ct)
            print(pt_file)
            with open(pt_file, 'w') as save:
                # save.write('\n'.join(dataset))
                save.write(json.dumps(dataset))
                p_ct += 1
                dataset = []


def _format_arxiv_to_lines(params):
    def load_arxiv_json(src_json, set, lower=True):
        source = []
        # flag = False
        id = json.load(open(src_json))['id']
        for sent_info in json.load(open(src_json))['inputs']:
            tokens = sent_info['tokens']
            if (lower):
                tokens = [t.lower() for t in tokens]
            source.append(tokens)

        tgt_txt_path = src_json.split('arxiv/')[0] + 'arxiv/' + 'human-abstracts/' + set + '/' + id + '.txt'
        with open(tgt_txt_path, mode='r') as f:
            abs_text = f.read()
        abs_text = abs_text.strip()

        tknized_abs = tokenize_with_corenlp(abs_text, id)

        return id, source, tknized_abs

    f, corpus_type, args = params
    source, tgt, id = load_arxiv_json(f, corpus_type, args.lower)
    return {'id': id, 'src': source, 'tgt': tgt}


def format_xsum_to_lines(args):
    if (args.dataset != ''):
        datasets = [args.dataset]
    else:
        datasets = ['train', 'test', 'valid']

    corpus_mapping = json.load(open(pjoin(args.raw_path, 'XSum-TRAINING-DEV-TEST-SPLIT-90-5-5.json')))

    for corpus_type in datasets:
        mapped_fnames = corpus_mapping[corpus_type]
        root_src = pjoin(args.raw_path, 'restbody')
        root_tgt = pjoin(args.raw_path, 'firstsentence')
        # realnames = [fname.split('.')[0] for fname in os.listdir(root_src)]
        realnames = mapped_fnames

        a_lst = [(root_src, root_tgt, n) for n in realnames]
        pool = Pool(args.n_cpus)
        dataset = []
        p_ct = 0
        for d in pool.imap_unordered(_format_xsum_to_lines, a_lst):
            if (d is None):
                continue
            dataset.append(d)
            if (len(dataset) > args.shard_size):
                pt_file = "{:s}.{:s}.{:d}.json".format(args.save_path, corpus_type, p_ct)
                with open(pt_file, 'w') as save:
                    save.write(json.dumps(dataset))
                    p_ct += 1
                    dataset = []

        pool.close()
        pool.join()
        if (len(dataset) > 0):
            pt_file = "{:s}.{:s}.{:d}.json".format(args.save_path, corpus_type, p_ct)
            with open(pt_file, 'w') as save:
                save.write(json.dumps(dataset))
                p_ct += 1
                dataset = []


def _format_xsum_to_lines(params):
    src_path, root_tgt, name = params
    f_src = pjoin(src_path, name + '.restbody')
    f_tgt = pjoin(root_tgt, name + '.fs')
    if (os.path.exists(f_src) and os.path.exists(f_tgt)):
        print(name)
        source = []
        for sent in open(f_src):
            source.append(sent.split())
        tgt = []
        for sent in open(f_tgt):
            tgt.append(sent.split())
        return {'src': source, 'tgt': tgt}
    return None
