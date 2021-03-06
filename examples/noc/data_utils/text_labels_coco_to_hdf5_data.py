#!/usr/bin/env python
# 4096d image fc7 + text labels to hdf

import numpy as np
import os
import random
random.seed(3)
import sys

from hdf5_npsequence_generator import SequenceGenerator, HDF5SequenceWriter

# UNK_IDENTIFIER is the word used to identify unknown words
UNK_IDENTIFIER = 'unk'

# start every sentence in a new array, pad if <max
MAX_FRAMES = 1
MAX_WORDS = 20

"""Filenames has file with image/frame paths for vidids
   and sentences with video ids"""
class fc7SequenceGenerator(SequenceGenerator):
  def __init__(self, filenames, vocab_filename, batch_num_streams=1, max_frames=MAX_FRAMES,
               align=True, shuffle=True, pad=True, truncate=True):
    self.max_frames = max_frames
    self.lines = []
    num_empty_lines = 0
    self.vid_poolfeats = {} # listofdict [{}]
    self.vid_labels = {}
    self.image_ids = {}

    for imgid_file, featfiles, sentfiles, label_files in filenames:
      with open(imgid_file, 'r') as infd:
        image_ids = infd.read().splitlines()
      self.image_ids = dict.fromkeys(image_ids, 1)
      for poolfeatfile in featfiles:
        print 'Reading features from file: %s' % poolfeatfile
        with open(poolfeatfile, 'rb') as poolfd:
          # each line has the fc7 mean of 1 video
          for line in poolfd:
            line = line.strip()
            id_feat = line.split(',')
            img_id = id_feat[0]
            line = ','.join(id_feat[1:])
            if img_id in self.image_ids:
              if img_id not in self.vid_poolfeats:
                self.vid_poolfeats[img_id]=[]
              self.vid_poolfeats[img_id].append(line)

      for sentfile in sentfiles:
        print 'Reading sentences in: %s' % sentfile
        with open(sentfile, 'r') as sentfd:
          for line in sentfd:
            line = line.strip()
            id_sent = line.split('\t')
            assert len(id_sent)==2
            if len(id_sent)<2:
              num_empty_lines += 1
              continue
            img_id = id_sent[0].split('#')[0]
            if img_id in self.image_ids:
              self.lines.append((img_id, id_sent[1]))
        if num_empty_lines > 0:
          print 'Warning: ignoring %d empty lines.' % num_empty_lines
      for labelfile in label_files:
        print 'Reading labels in: %s' % labelfile
        with open(labelfile, 'r') as sentfd:
          for line in sentfd:
            line = line.strip()
            id_sent = line.split('\t')
            if len(id_sent)<2:
              num_empty_lines += 1
              continue
            #labels = id_sent[1].split(',')
            if id_sent[0] in self.image_ids:
              self.vid_labels[id_sent[0]] = id_sent[1]
    self.line_index = 0
    self.num_resets = 0
    self.num_truncates = 0
    self.num_pads = 0
    self.num_outs = 0
    self.frame_list = []
    self.vocabulary = {}
    self.vocabulary_inverted = []
    # initialize vocabulary
    self.init_vocabulary(vocab_filename)
    SequenceGenerator.__init__(self)
    self.batch_num_streams = batch_num_streams  # needed in hdf5 to seq
    # make the number of image/sentence pairs a multiple of the buffer size
    # so each timestep of each batch is useful and we can align the images
    if align:
      num_pairs = len(self.lines)
      remainder = num_pairs % BUFFER_SIZE
      if remainder > 0:
        num_needed = BUFFER_SIZE - remainder
        for i in range(num_needed):
          choice = random.randint(0, num_pairs - 1)
          self.lines.append(self.lines[choice])
      assert len(self.lines) % BUFFER_SIZE == 0
    if shuffle:
      random.shuffle(self.lines)
    self.pad = pad
    self.truncate = truncate

  def streams_exhausted(self):
    return self.num_resets > 0

  def init_vocabulary(self, vocab_filename):
    print "Initializing the vocabulary."
    if os.path.isfile(vocab_filename):
      with open(vocab_filename, 'rb') as vocab_file:
        self.init_vocab_from_file(vocab_file)
    else:
      print "Error: No vocab file!"

  def init_vocab_from_file(self, vocab_filedes):
    # initialize the vocabulary with the UNK word
    self.vocabulary = {UNK_IDENTIFIER: 0}
    self.vocabulary_inverted = [UNK_IDENTIFIER]
    num_words_dataset = 0
    for line in vocab_filedes.readlines():
      split_line = line.split()
      word = split_line[0]
      print word
      #if unicode(word) == UNK_IDENTIFIER:
      if word == UNK_IDENTIFIER:
        continue
      else:
        assert word not in self.vocabulary
      num_words_dataset += 1
      self.vocabulary[word] = len(self.vocabulary_inverted)
      self.vocabulary_inverted.append(word)
    num_words_vocab = len(self.vocabulary.keys())
    print ('Initialized vocabulary from file with %d unique words ' +
           '(from %d total words in dataset).') % \
          (num_words_vocab, num_words_dataset)
    assert len(self.vocabulary_inverted) == num_words_vocab

  def dump_video_file(self, vidid_order_file, frame_seq_label_file):
    print 'Dumping vidid order to file: %s' % vidid_order_file
    with open(vidid_order_file,'wb') as vidid_file:
      for vidid, line in self.lines:
        word_count = len(line.split())
        # frame_count = len(self.vid_frames[vidid])
        #total_count = word_count +frame_count
        vidid_file.write('%s\t%d\n' % (vidid, word_count))#, frame_count, total_count))
    # print 'Dumping frame sequence label to file: %s' % frame_seq_label_file
    # # for every video we dump frames and pads for the sentence
    # with open(frame_seq_label_file,'wb') as frame_file:
    #   for frame_path, label in self.frame_list:
    #     frame_file.write('%s %d\n' % (frame_path, label)) 
    print 'Done.' 

  def next_line(self):
    num_lines = float(len(self.lines))
    if self.line_index == 1 or self.line_index == num_lines or self.line_index % 10000 == 0:
      print 'Processed %d/%d (%f%%) lines' % (self.line_index, num_lines,
                                              100 * self.line_index / num_lines)
    self.line_index += 1
    if self.line_index == num_lines:
      self.line_index = 0
      self.num_resets += 1

  def get_pad_value(self, stream_name):
    return 0

  """label_list: line with "," separated list of positive labels
     return: list of 0/1s dim:#lexical labels, 1 for +ve"""
  def labels_to_values(self, label_list):
    pos_labels = label_list.split(',')
    label_arr = np.zeros((1, len(self.vocabulary)+1))
    label_indices = [self.vocabulary[label]+1 for label in pos_labels]
    label_arr[0, label_indices] = 1
    return label_arr

  def float_line_to_stream(self, line):
    return map(float, line.split(','))

  # we have pooled fc7 features already in the file
  def get_streams(self):
    vidid, line = self.lines[self.line_index]
    assert vidid in self.vid_poolfeats
    text_mean_fc7 = self.vid_poolfeats[vidid][0] # list inside dict
    mean_fc7 = self.float_line_to_stream(text_mean_fc7)
    labels = self.labels_to_values(self.vid_labels[vidid])

    self.num_outs += 1
    out = {}
    out['mean_fc7'] = np.array(mean_fc7).reshape(1, len(mean_fc7))
    out['labels'] = labels
    self.next_line()
    return out


class TextSequenceGenerator(SequenceGenerator):
  def __init__(self, fsg_lines, vocab_filename, batch_num_streams=8, max_words=MAX_WORDS,
               pad=True, truncate=True):
    self.max_words = max_words
    self.lines = fsg_lines
    self.line_index = 0
    self.num_resets = 0
    self.num_truncates = 0
    self.num_pads = 0
    self.num_outs = 0
    self.vocabulary = {}
    self.vocabulary_inverted = []
    self.vocab_counts = []
    # initialize vocabulary
    self.init_vocabulary(vocab_filename)
    SequenceGenerator.__init__(self)
    self.batch_num_streams = batch_num_streams # needed in hdf5 to seq
    self.pad = pad
    self.truncate = truncate
    self.negative_one_padded_streams = frozenset(('target_sequence'))

  def streams_exhausted(self):
    return self.num_resets > 0

  def init_vocabulary(self, vocab_filename):
    print "Initializing the vocabulary."
    if os.path.isfile(vocab_filename):
      with open(vocab_filename, 'rb') as vocab_file:
        self.init_vocab_from_file(vocab_file)
    else:
      self.init_vocabulary_from_data(vocab_filename)


  def init_vocab_from_file(self, vocab_filedes):
    # initialize the vocabulary with the UNK word
    self.vocabulary = {UNK_IDENTIFIER: 0}
    self.vocabulary_inverted = [UNK_IDENTIFIER]
    num_words_dataset = 0
    for line in vocab_filedes.readlines():
      split_line = line.split()
      word = split_line[0]
      print word
      #if unicode(word) == UNK_IDENTIFIER:
      if word == UNK_IDENTIFIER:
        continue
      else:
        assert word not in self.vocabulary
      num_words_dataset += 1
      self.vocabulary[word] = len(self.vocabulary_inverted)
      self.vocabulary_inverted.append(word)
    num_words_vocab = len(self.vocabulary.keys())
    print ('Initialized vocabulary from file with %d unique words ' +
           '(from %d total words in dataset).') % \
          (num_words_vocab, num_words_dataset)
    assert len(self.vocabulary_inverted) == num_words_vocab

  def init_vocabulary_from_data(self, vocab_filename):
    print 'Initializing the vocabulary from full data'
    assert len(self.lines) > 0
    # initialize the vocabulary with the UNK word if new
    self.vocabulary = {UNK_IDENTIFIER: 0}
    self.vocabulary_inverted.append(UNK_IDENTIFIER)
    # count frequency of word in data
    self.vocab_counts.append(0)
      
    num_words_dataset = 0
    for vidid, line in self.lines:
      split_line = line.split()
      num_words_dataset += len(split_line)
      for word in split_line:
        if word in self.vocabulary:
          self.vocab_counts[self.vocabulary[word]] += 1
        else:
          self.vocabulary_inverted.append(word)
          self.vocabulary[word] = len(self.vocab_counts)
          self.vocab_counts.append(1)
          
    num_words_vocab = len(self.vocabulary.keys())
    print ('Initialized the vocabulary from data with %d unique words ' +
           '(from %d total words in dataset).') % (num_words_vocab, num_words_dataset)
    assert len(self.vocab_counts) == num_words_vocab
    assert len(self.vocabulary_inverted) == num_words_vocab
    if self.vocab_counts[self.vocabulary[UNK_IDENTIFIER]] == 0:
      print 'Warning: the count for the UNK identifier "%s" was 0.' % UNK_IDENTIFIER

  def dump_vocabulary(self, vocab_filename):
    print 'Dumping vocabulary to file: %s' % vocab_filename
    with open(vocab_filename, 'wb') as vocab_file:
      for word in self.vocabulary_inverted:
        vocab_file.write('%s\n' % word)
    print 'Done.'

  def next_line(self):
    num_lines = float(len(self.lines))
    if self.line_index == 1 or self.line_index == num_lines or self.line_index % 10000 == 0:
      print 'Processed %d/%d (%f%%) lines' % (self.line_index, num_lines,
                                              100 * self.line_index / num_lines)
    self.line_index += 1
    if self.line_index == num_lines:
      self.line_index = 0
      self.num_resets += 1

  def line_to_stream(self, sentence):
    stream = []
    for word in sentence.split():
      word = word.strip()
      if word in self.vocabulary:
        stream.append(self.vocabulary[word])
      else:  # unknown word; append UNK
        stream.append(self.vocabulary[UNK_IDENTIFIER])
    # increment the stream -- 0 will be the EOS character
    stream = [s + 1 for s in stream]
    return stream

  def get_pad_value(self, stream_name):
    return -1 if stream_name in self.negative_one_padded_streams else 0

  def get_streams(self):
    vidid, line = self.lines[self.line_index]
    stream = self.line_to_stream(line)
    pad = self.max_words - (len(stream) + 1) if self.pad else 0
    truncated = False
    if pad < 0:
      print 'Video id: {0} Num words: {1}'.format(vidid, len(stream))
      # truncate words to max
      stream = stream[:self.max_words-1]
      truncated = True
      pad = 0
      # print 'AfterTruncate: Num words: {1}'.format(vidid, len(stream))
    self.num_truncates += truncated

    if pad > 0: self.num_pads += 1
    self.num_outs += 1
    out = {}
    # add an extra item in the beginning
    out['cont_sentence'] = [0] + [1] * len(stream) + [0] * pad
    # 0 pad inputs not -1
    out['input_sentence'] = [0] + stream + [0] * pad
    out['target_sequence'] = stream + [0] + [-1] * pad
    # out['target_sentence'] = stream + [0] + [-1] * pad
    output_length = len(out['input_sentence'])
    assert len(out['cont_sentence']) == output_length
    assert len(out['target_sequence']) == output_length
    self.next_line()
    return out


VIDEO_STREAMS = 1
BUFFER_SIZE = 32 # TEXT streams
BATCH_STREAM_LENGTH = 1000 # (21 * 50)
SETTING = 'data/coco2014'
# OUTPUT_BASIS_DIR = '{0}/hdf5/buffer_{1}_rm8obj_label72k_{2}'.format(SETTING,
OUTPUT_BASIS_DIR = '{0}/hdf5/buffer_{1}_rm8newobj_label72k_{2}'.format(SETTING,
VIDEO_STREAMS, MAX_FRAMES)
# OUTPUT_TEXT_DIR = '{0}/hdf5/buffer_{1}_rm8obj_label72k_{2}'.format(SETTING, BUFFER_SIZE, MAX_WORDS)
OUTPUT_TEXT_DIR = '{0}/hdf5/buffer_{1}_rm8newobj_label72k_{2}'.format(SETTING, BUFFER_SIZE, MAX_WORDS)
VOCAB = './surf_intersect_glove.txt'
OUTPUT_BASIS_DIR_PATTERN = '%s/%%s_batches' % OUTPUT_BASIS_DIR
OUTPUT_TEXT_DIR_PATTERN = '%s/%%s_batches' % OUTPUT_TEXT_DIR
POOLFEAT_FILE_PATTERN = 'data/coco2014/coco2014_{0}_vgg_fc7.txt'
SENTS_FILE_PATTERN = 'data/coco2014/sents/coco_sentences_{0}_tokens.txt'
LABEL_FILE_PATTERN = 'data/coco2014/sents/labels_glove72k_{0}.txt' #train2014
# IMAGEID_FILE_PATTERN = 'data/coco2014/coco_rm8objs_image_list_{0}.txt'
IMAGEID_FILE_PATTERN = 'data/coco2014/cvpr17_rm8newobjs/coco_rm8newobjs_image_list_{0}.txt'

def preprocess_dataset(split_name, data_split_name, batch_stream_length, aligned=False):
  if split_name == 'train':
    imgid_file = IMAGEID_FILE_PATTERN.format('train2014')
    feat_files = [POOLFEAT_FILE_PATTERN.format('trainvallstm')]
    sent_files = [SENTS_FILE_PATTERN.format('train2014')]
    label_file = [LABEL_FILE_PATTERN.format('train2014')]
  elif split_name == 'valid':
    imgid_file = IMAGEID_FILE_PATTERN.format('val2014')
    feat_files = [POOLFEAT_FILE_PATTERN.format('trainvallstm'),
                  POOLFEAT_FILE_PATTERN.format('mytest')]
    sent_files = [SENTS_FILE_PATTERN.format('trainvallstm'),
                  SENTS_FILE_PATTERN.format('mytest')]
    label_file = [LABEL_FILE_PATTERN.format('vallstm2014'),
                  LABEL_FILE_PATTERN.format('mytest')]
  else:
    print 'Error. Invalid data_split_name: %s' % data_split_name
    return
  filenames = [(imgid_file, feat_files, sent_files, label_file)]

  vocab_filename = VOCAB
  output_basis_path = OUTPUT_BASIS_DIR_PATTERN % split_name
  aligned = True
  fsg = fc7SequenceGenerator(filenames, vocab_filename, VIDEO_STREAMS,
         max_frames=MAX_FRAMES, align=aligned, shuffle=True, pad=aligned,
         truncate=aligned)
  fsg.batch_stream_length = batch_stream_length
  writer = HDF5SequenceWriter(fsg, output_dir=output_basis_path)
  writer.write_to_exhaustion()
  writer.write_filelists()
  output_text_path = OUTPUT_TEXT_DIR_PATTERN % split_name
  fsg_lines = fsg.lines
  tsg = TextSequenceGenerator(fsg_lines, vocab_filename, BUFFER_SIZE,
         max_words=MAX_WORDS, pad=aligned, truncate=aligned)
  tsg.batch_stream_length = batch_stream_length
  writer = HDF5SequenceWriter(tsg, output_dir=output_text_path)
  writer.write_to_exhaustion()
  writer.write_filelists()
  if not os.path.isfile(vocab_filename):
    print "Vocabulary not found"

def process_splits():
  DATASETS = [ # split_name, data_split_name, aligned
      ('valid', 'mytest', True),
      # ('train', 'trainvallstm', True),
      # ('test', 'test', False),
  ]
  for split_name, data_split_name, aligned in DATASETS:
    preprocess_dataset(split_name, data_split_name, BATCH_STREAM_LENGTH,aligned)

if __name__ == "__main__":
  process_splits()
