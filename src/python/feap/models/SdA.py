import numpy
import theano
import theano.tensor as T
from theano.tensor.shared_randomstreams import RandomStreams
from feap.core.model import PredictorModel
from feap.models.dA import DenoisingAutoencoder
from feap.models.mlp import HiddenLayer
from feap.models.regression import LogisticRegression

class SdA(PredictorModel):
    def __init__(self, numpy_rng, theano_ring=None, in_size=784, hidden_sizes=[500, 500], out_size=10,
                 corruption_levels=[0.1, 0.1], unsupervised_epochs=100, unsupervised_learning_rate=.001):
        super(SdA,self).__init__()
        self.sigmoid_layers = []
        self.dA_layers = []
        self.params = []
        self.n_layers = len(hidden_sizes)
        self.unsupervised_epochs=unsupervised_epochs
        self.unsupervised_learning_rate=unsupervised_learning_rate

        assert self.n_layers > 0

        if theano_ring is None:
            theano_ring = RandomStreams(numpy_rng.randint(2 ** 30))

        self.input = T.matrix('x')  # the data is presented as rasterized images
        learning_rate = T.scalar('learning_rate')  # learning rate to use

        self.pretrain_fns = []

        for i in xrange(self.n_layers):
            # construct the sigmoidal layer

            # the size of the input is either the number of hidden units of
            # the layer below or the input size if we are on the first layer
            if i == 0:
                input_size = in_size
            else:
                input_size = hidden_sizes[i - 1]

            # the input to this layer is either the activation of the hidden
            # layer below or the input of the SdA if you are on the first
            # layer
            if i == 0:
                layer_input = self.input
            else:
                layer_input = self.sigmoid_layers[-1].output

            sigmoid_layer = HiddenLayer(numpy_rng,
                layer_input,
                input_size,
                hidden_sizes[i],
                activation=T.nnet.sigmoid)
            # add the layer to our list of layers
            self.sigmoid_layers.append(sigmoid_layer)
            # its arguably a philosophical question...
            # but we are going to only declare that the parameters of the
            # sigmoid_layers are parameters of the StackedDAA
            # the visible biases in the dA are parameters of those
            # dA, but not the SdA
            self.params.extend(sigmoid_layer.params)

            # Construct a denoising autoencoder that shared weights with this
            # layer
            dA_layer = DenoisingAutoencoder(numpy_rng, input_size,hidden_sizes[i],theano_rng=theano_ring,
                input=layer_input, W=sigmoid_layer.W, bhid=sigmoid_layer.b, corruption_level=corruption_levels[i])
            self.pretrain_fns.append(dA_layer.train_model)
            self.dA_layers.append(dA_layer)

        # We now need to add a logistic layer on top of the MLP
        self.logLayer = LogisticRegression(
            hidden_sizes[-1], out_size, input=self.sigmoid_layers[-1].output)

        self.y=self.logLayer.y

        self.params.extend(self.logLayer.params)
        # construct a function that implements one step of finetunining

        # compute the cost for second phase of training,
        # defined as the negative log likelihood
        self.cost = self.logLayer.negative_log_likelihood

        self.finetune_function=theano.function(inputs=[self.input,self.y,theano.Param(learning_rate, default=0.13)],
            outputs=self.cost(),
            updates=self.get_updates(learning_rate),
            givens={},
            name='train')

        self.pred_input = T.vector('pred_input')
        self.predict = theano.function(inputs=[self.pred_input], outputs=self.get_prediction(self.pred_input))

    def transform(self, data):
        layer_input=data
        for i in xrange(self.n_layers):
            layer_input=self.sigmoid_layers[i].get_output(layer_input)
        return layer_input

    def get_prediction(self, input):
        layer_input=input
        for i in xrange(self.n_layers):
            layer_input=self.sigmoid_layers[i].get_output(layer_input)
        return self.logLayer.get_prediction(self.logLayer.get_class_probabilities(layer_input))

    def errors(self, y):
        super(SdA,self).errors(y)
        return self.logLayer.errors(self.y)

    def train_unsupervised(self, train_set_x, learning_rate=.13):
        layer_cost = []
        input=train_set_x
        for i in xrange(self.n_layers):
            layer_cost.append(self.pretrain_fns[i](input,learning_rate=learning_rate))
            next_input=numpy.zeros((input.shape[0],self.dA_layers[i].hidden_size))
            for j in xrange(input.shape[0]):
                next_input[j,:]=self.dA_layers[i].transform(input[j,:])
            input=next_input
        return numpy.mean(layer_cost)

    def train(self, data, learning_rate=.13):
        if self.is_unsupervised:
            train_set_x = numpy.array(data)
            cost=0
            for i in xrange(self.unsupervised_epochs):
                cost=self.train_unsupervised(train_set_x, learning_rate=self.unsupervised_learning_rate)
            return cost
        else:
            train_set_x = numpy.array([x[0] for x in data])
            train_set_y = numpy.array([x[1] for x in data])
            self.train_unsupervised(train_set_x, learning_rate=learning_rate)
            return self.finetune_function(train_set_x,train_set_y,learning_rate=learning_rate)

