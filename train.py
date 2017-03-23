#!/usr/bin/env python3
import argparse
import json
import math
import time

import torch
from torch.autograd import Variable

import data
import model


def batchify(data, bsz, cuda=False):
    # Work out how cleanly we can divide the dataset into bsz parts.
    nbatch = data.size(0) // bsz
    # Trim off any extra elements that wouldn't cleanly fit (remainders).
    data = data.narrow(0, 0, nbatch * bsz)
    # Evenly divide the data across the bsz batches.
    data = data.view(bsz, -1).t().contiguous()
    if cuda:
        data = data.cuda()
    return data


def repackage_hidden(h):
    # pylint:disable=line-too-long
    """Wraps hidden states in new Variables, to detach them from their history."""
    if type(h) == Variable:
        return Variable(h.data)
    else:
        return tuple(repackage_hidden(v) for v in h)


def get_batch(source, i, bptt, evaluation=False):
    seq_len = min(bptt, len(source) - 1 - i)
    data = Variable(source[i:i + seq_len], volatile=evaluation)
    target = Variable(source[i + 1:i + 1 + seq_len].view(-1))
    return data, target


def evaluate(model, criterion, data_source, vocab, hps):
    # Turn on evaluation mode which disables dropout.
    model.eval()
    total_loss = 0
    ntokens = vocab.size()
    hidden = model.init_hidden(hps['batch_size'])
    for i in range(0, data_source.size(0) - 1, hps['bptt']):
        data, targets = get_batch(data_source, i, hps['bptt'], evaluation=True)
        output, hidden = model(data, hidden)
        output_flat = output.view(-1, ntokens)
        total_loss += len(data) * criterion(output_flat, targets).data
        hidden = repackage_hidden(hidden)
    return total_loss[0] / len(data_source)


def train_epoch(model, criterion, train_data, vocab, hps, lr, epoch):
    model.train()
    total_loss = 0
    start_time = time.time()
    ntokens = vocab.size()
    hidden = model.init_hidden(hps['batch_size'])

    last_log_batch = 0
    for batch, i in enumerate(range(0, train_data.size(0) - 1, hps['bptt'])):
        data, targets = get_batch(train_data, i, hps['bptt'])
        # pylint:disable=line-too-long
        # Starting each batch, we detach the hidden state from how it was previously produced.
        # If we didn't, the model would try backpropagating all the way to
        # start of the dataset.
        hidden = repackage_hidden(hidden)
        model.zero_grad()
        output, hidden = model(data, hidden)
        loss = criterion(output.view(-1, ntokens), targets)
        loss.backward()

        # `clip_grad_norm` helps prevent the exploding gradient problem in RNNs / LSTMs.
        torch.nn.utils.clip_grad_norm(model.parameters(), hps['clip'])
        for p in model.parameters():
            p.data.add_(-lr, p.grad.data)

        total_loss += loss.data

        if (batch % hps['log_interval'] == 0 and batch > 0) or (
                batch == len(train_data) // hps['bptt']):
            cur_loss = total_loss[0] / (batch - last_log_batch)
            last_log_batch = batch

            elapsed = time.time() - start_time
            print(
                '| epoch {:3d} | {:5d}/{:5d} batches | lr {:02.2f} | ms/batch {:5.2f} | '
                'loss {:5.2f} | ppl {:8.2f}'.format(
                    epoch, batch,
                    len(train_data) // hps['bptt'], lr,
                    elapsed * 1000 / hps['log_interval'], cur_loss,
                    math.exp(cur_loss)))
            total_loss = 0
            start_time = time.time()


def train(hps):
    corpus = data.Corpus(hps['corpus'])

    vocab = data.Vocab(corpus.export_char_list())
    vocab.save(hps['vocab_file'])
    vocab = data.Vocab.load(hps['vocab_file'])

    corpus.tokenize(vocab)

    ntokens = vocab.size()
    m = model.RNNModel(
        ntokens,
        hps['emsize'],
        hps['nhid'],
        hps['nlayers'],
        hps['dropout'],
        hps['tied'], )
    if hps['cuda']:
        m.cuda()

    criterion = torch.nn.CrossEntropyLoss()

    train_data = batchify(corpus.train_ids, hps['batch_size'], hps['cuda'])
    eval_data = batchify(corpus.eval_ids, hps['batch_size'], hps['cuda'])

    lr = hps['lr']
    best_val_loss = None

    # At any point you can hit Ctrl + C to break out of training early.
    print('-' * 89)
    try:
        for epoch in range(1, hps['epochs'] + 1):
            epoch_start_time = time.time()
            train_epoch(m, criterion, train_data, vocab, hps, lr, epoch)
            val_loss = evaluate(m, criterion, eval_data, vocab, hps)
            print('-' * 89)
            print(
                '| end of epoch {:3d} | time: {:5.2f}s | valid loss {:5.2f} | '
                'valid ppl {:8.2f}'.format(
                    epoch, (time.time() - epoch_start_time), val_loss,
                    math.exp(val_loss)))
            print('-' * 89)
            # Save the model if the validation loss is the best we've seen so
            # far.
            if not best_val_loss or val_loss < best_val_loss:
                with open(hps['save'], 'wb') as f:
                    torch.save(m, f)
                best_val_loss = val_loss
            else:
                # Anneal the learning rate if no improvement has been seen in
                # the validation dataset.
                lr /= 4
    except KeyboardInterrupt:
        print('-' * 89)
        print('Exiting from training early')


def main():
    parser = argparse.ArgumentParser(description='PyTorch Language Model')
    parser.add_argument('--hps-file', type=str, required=True,
                        help='location of hyper parameter json file.')

    args = parser.parse_args()
    hps = json.load(open(args.hps_file))
    train(hps)

if __name__ == '__main__':
    main()
