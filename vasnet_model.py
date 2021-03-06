__author__ = 'Jiri Fajtl'
__email__ = 'ok1zjf@gmail.com'
__version__= '3.6'
__status__ = "Research"
__date__ = "1/12/2018"
__license__= "MIT License"


import torch
import torch.nn as nn
import torch.nn.functional as F
from config import  *
from layer_norm import  *
import numpy as np



class SelfAttention(nn.Module):

    def __init__(self, apperture=-1, ignore_itself=False, input_size=1024, output_size=1024):
        super(SelfAttention, self).__init__()

        self.apperture = apperture
        self.ignore_itself = ignore_itself

        self.m = input_size
        self.output_size = output_size

        self.K = nn.Linear(in_features=self.m, out_features=self.output_size, bias=False)
        self.Q = nn.Linear(in_features=self.m, out_features=self.output_size, bias=False)
        self.V = nn.Linear(in_features=self.m, out_features=self.output_size, bias=False)
        self.output_linear = nn.Linear(in_features=self.output_size, out_features=self.m, bias=False)

        self.drop50 = nn.Dropout(0.5)



    def forward(self, x):
        n = x.shape[0]  # sequence length

        K = self.K(x)  # ENC (n x m) => (n x H) H= hidden size
        Q = self.Q(x)  # ENC (n x m) => (n x H) H= hidden size
        V = self.V(x)

        Q *= 0.06
        logits = torch.matmul(Q, K.transpose(1,0))

        if self.ignore_itself:
            # Zero the diagonal activations (a distance of each frame with itself)
            logits[torch.eye(n).byte()] = -float("Inf")

        if self.apperture > 0:
            # Set attention to zero to frames further than +/- apperture from the current one
            onesmask = torch.ones(n, n)
            trimask = torch.tril(onesmask, -self.apperture) + torch.triu(onesmask, self.apperture)
            logits[trimask == 1] = -float("Inf")

        att_weights_ = nn.functional.softmax(logits, dim=-1)
        weights = self.drop50(att_weights_)
        y = torch.matmul(V.transpose(1,0), weights).transpose(1,0)
        y = self.output_linear(y)

        return y, att_weights_



class VASNet(nn.Module):

    def __init__(self,in_dim = 1024, hid_dim = 1024):
        super(VASNet, self).__init__()

        # self.m = 1024 # cnn features size
        self.m = in_dim # cnn features size
        # self.hidden_size = 1024
        self.hidden_size = hid_dim

        self.att = SelfAttention(input_size=self.m, output_size=self.m)
        self.ka = nn.Linear(in_features=self.m,     out_features=self.hidden_size)
        self.kb = nn.Linear(in_features=self.ka.out_features, out_features=self.hidden_size)
        self.kc = nn.Linear(in_features=self.kb.out_features, out_features=self.hidden_size)
        self.kd = nn.Linear(in_features=self.ka.out_features, out_features=1)

        self.sig = nn.Sigmoid()
        self.relu = nn.ReLU()
        self.drop50 = nn.Dropout(0.5)
        self.softmax = nn.Softmax(dim=0)
        self.layer_norm_y = LayerNorm(self.m)
        self.layer_norm_ka = LayerNorm(self.ka.out_features)


    def forward(self, x, seq_len):

        m = x.shape[2] # Feature size

        # Place the video frames to the batch dimension to allow for batch arithm. operations.
        # Assumes input batch size = 1.
        x = x.view(-1, m)
        y, att_weights_ = self.att(x)

        y = y + x
        y = self.drop50(y)
        y = self.layer_norm_y(y)

        # Frame level importance score regression
        # Two layer NN
        y = self.ka(y)
        y = self.relu(y)
        y = self.drop50(y)
        y = self.layer_norm_ka(y)

        y = self.kd(y)
        y = self.sig(y)
        y = y.view(1, -1)

        return y, att_weights_

class MultiVASNet(nn.Module):
    "This Model uses the default torch attention layers instead of using self made layers"

    def __init__(self,n_heads=4,second_layer_dim=1024,use_extra_linear = True):
        super(MultiVASNet,self).__init__()
        self.attn = nn.MultiheadAttention(1024,n_heads,dropout=0.5)
        self.drop = nn.Dropout(0.5)
        self.use_extra_linear = use_extra_linear
        if use_extra_linear:
            self.extra_linear = nn.Linear(in_features=1024,out_features=1024,bias=False)
        self.fc = nn.Sequential(
                    nn.LayerNorm(1024),
                    nn.Dropout(0.5),
                    nn.Linear(1024,second_layer_dim),
                    nn.ReLU(),
                    nn.Dropout(0.5),
                    nn.LayerNorm(second_layer_dim),
                    nn.Dropout(0.5),
                    nn.Linear(second_layer_dim,1),
                    nn.Sigmoid()
                    )

    def forward(self,x,seq_len):
        m = x.shape[2] # Feature size

        # Place the video frames to the batch dimension to allow for batch arithm. operations.
        # Assumes input batch size = 1.
        x = x.expand(*x.shape)
        assert not torch.equal(0.06*x,x)
        y, att_weights_ = self.attn(0.06*x,x,x,need_weights=True)
        y = y + x
        if self.use_extra_linear:
            y = (self.fc(self.extra_linear(y)))
        else:
            y = self.fc(y)
        return y.view(1,-1),att_weights_

class MultiVASNetWithPageRank(nn.Module):
    def __init__(self,n_heads=4,second_layer_dim=1024):
        super().__init__()
        # second_layer_dim += 1
        self.attn = nn.MultiheadAttention(1024,n_heads,dropout=0.4)
        self.drop = nn.Dropout(0.5)
        self.fc1 = nn.Sequential(
                    nn.LayerNorm(1024+1),
                    nn.Dropout(0.5),
                    nn.Linear(1024+1,second_layer_dim),
                    nn.ReLU(),
                    nn.Dropout(0.5),
                    nn.LayerNorm(second_layer_dim),
                    nn.Dropout(0.5)
                    )
        self.fc2 = nn.Sequential(
                    nn.Linear(second_layer_dim+1,1),
                    nn.Sigmoid()
                    )

    def pagerank(self,M,d=0.15,eps=1e-2):
        N = M.shape[1]
        v = torch.randn(N, 1).cuda()
        v = v / torch.norm(v, 1)
        last_v = torch.ones((N, 1)).cuda() * 100

        while torch.norm(v - last_v, 2) > eps:
            last_v = v
            v = d * torch.matmul(M, v) + (1 - d) / N
        return v

    def forward(self,x,seq_len):
        m = x.shape[2] # Feature size

        # Place the video frames to the batch dimension to allow for batch arithm. operations.
        # Assumes input batch size = 1.
        x = x.expand(*x.shape)
        assert not torch.equal(0.06*x,x)
        y, att_weights_ = self.attn(0.06*x,x,x,need_weights=True)
        p = self.pagerank(att_weights_).permute(1,0,2)
        y = y + x
        y = torch.cat((p,y),2)
        y = self.fc1(y)
        y = torch.cat((p,y),2)
        y = self.fc2(y)
        return y.view(1,-1),att_weights_


class CatMultiVASNet(nn.Module):
    def __init__(self,n_heads=4):
        super(CatMultiVASNet,self).__init__()
        self.attn = nn.MultiheadAttention(1024,n_heads,dropout=0.4)
        self.drop = nn.Dropout(0.5)
        self.fc = nn.Sequential(
                            nn.LayerNorm(1024),
                            nn.Dropout(0.5),
                            nn.Linear(2048,512),
                            nn.ReLU(),
                            nn.Dropout(0.5),
                            nn.LayerNorm(512),
                            nn.Dropout(0.5),
                            nn.Linear(512,1),
                            nn.Sigmoid()
                            )

    def forward(self,x,seq_len):
        m = x.shape[2] # Feature size

        # Place the video frames to the batch dimension to allow for batch arithm. operations.
        # Assumes input batch size = 1.
        x = x.expand(*x.shape)
        y, att_weights_ = self.attn(x,x,x,need_weights=True)
        y = torch.cat((y,x),-1)
        y = self.fc(y)
        return y.view(1,-1),att_weights_



if __name__ == "__main__":
    pass
