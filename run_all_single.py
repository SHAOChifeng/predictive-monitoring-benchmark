import pandas as pd
import numpy as np
import sys
from sklearn.metrics import roc_auc_score, precision_recall_fscore_support
from transformers.StaticTransformer import StaticTransformer
from transformers.LastStateTransformer import LastStateTransformer
from transformers.AggregateTransformer import AggregateTransformer
from transformers.IndexBasedTransformer import IndexBasedTransformer
from transformers.HMMDiscriminativeTransformer import HMMDiscriminativeTransformer
from transformers.HMMGenerativeTransformer import HMMGenerativeTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline, FeatureUnion
import dataset_confs

#datasets = ["bpic2011_f%s"%formula for formula in range(1,5)]
#datasets = ["bpic2015_%s_f%s"%(municipality, formula) for municipality in range(1,6) for formula in range(1,3)]
#datasets = ["insurance_activity", "insurance_followup"]
#datasets = ["traffic_fines_f%s"%formula for formula in range(1,4)]
datasets = ["bpic2011_f%s"%formula for formula in range(1,5)] + ["bpic2015_%s_f%s"%(municipality, formula) for municipality in range(1,6) for formula in range(1,3)] + ["traffic_fines_f%s"%formula for formula in range(1,4)]

#outfile = "results/results_all_single_bpic2011.csv"
#outfile = "results/results_all_single_bpic2015.csv"
#outfile = "results/results_all_single_insurance.csv"
#outfile = "results/results_all_single_traffic.csv"
outfile = "results/results_single_public.csv"

prefix_lengths = list(range(2,21))

train_ratio = 0.8
hmm_min_seq_length = 2
hmm_max_seq_length = None
hmm_n_iter = 30
hmm_n_states = 6
rf_n_estimators = 500
random_state = 22
fillna = True

methods_dict = {
    "single_laststate": ["static", "laststate"],
    "single_agg": ["static", "agg"],
    "single_hmm_disc": ["static", "hmm_disc"],
    "single_combined": ["static", "laststate", "agg", "hmm_disc"]}


def init_encoder(method):
    
    if method == "static":
        return StaticTransformer(case_id_col=case_id_col, cat_cols=static_cat_cols, num_cols=static_num_cols, fillna=fillna)
    
    elif method == "laststate":
        return LastStateTransformer(case_id_col=case_id_col, cat_cols=dynamic_cat_cols, num_cols=dynamic_num_cols, fillna=fillna)
    
    elif method == "agg":
        return AggregateTransformer(case_id_col=case_id_col, cat_cols=dynamic_cat_cols, num_cols=dynamic_num_cols, fillna=fillna)
    
    elif method == "hmm_disc":
        hmm_disc_encoder = HMMDiscriminativeTransformer(case_id_col=case_id_col, cat_cols=dynamic_cat_cols,
                                                        num_cols=dynamic_num_cols, n_states=hmm_n_states, label_col=label_col,
                                                        pos_label=pos_label, min_seq_length=hmm_min_seq_length,
                                                        max_seq_length=hmm_max_seq_length, random_state=random_state,
                                                        n_iter=hmm_n_iter, fillna=fillna)
        hmm_disc_encoder.fit(train.sort_values(timestamp_col, ascending=True))
        return hmm_disc_encoder
    
    elif method == "hmm_gen":
        hmm_gen_encoder = HMMGenerativeTransformer(case_id_col=case_id_col, cat_cols=dynamic_cat_cols,
                                                   num_cols=dynamic_num_cols, n_states=hmm_n_states,
                                                   min_seq_length=hmm_min_seq_length, max_seq_length=hmm_max_seq_length,
                                                   random_state=random_state, n_iter=hmm_n_iter, fillna=fillna)
        hmm_gen_encoder.fit(train.sort_values(timestamp_col, ascending=True))
        return hmm_gen_encoder
    
    else:
        print("Invalid encoder type")
        return None
    
    
##### MAIN PART ######    
with open(outfile, 'w') as fout:
    
    fout.write("%s;%s;%s;%s;%s\n"%("dataset", "method", "nr_events", "metric", "score"))
    
    for dataset_name in datasets:
        
        # read dataset settings
        case_id_col = dataset_confs.case_id_col[dataset_name]
        activity_col = dataset_confs.activity_col[dataset_name]
        timestamp_col = dataset_confs.timestamp_col[dataset_name]
        label_col = dataset_confs.label_col[dataset_name]
        pos_label = dataset_confs.pos_label[dataset_name]

        dynamic_cat_cols = dataset_confs.dynamic_cat_cols[dataset_name]
        static_cat_cols = dataset_confs.static_cat_cols[dataset_name]
        dynamic_num_cols = dataset_confs.dynamic_num_cols[dataset_name]
        static_num_cols = dataset_confs.static_num_cols[dataset_name]
        
        data_filepath = dataset_confs.filename[dataset_name]
        
        # specify data types
        dtypes = {col:"object" for col in dynamic_cat_cols+static_cat_cols+[case_id_col, label_col, timestamp_col]}
        for col in dynamic_num_cols + static_num_cols:
            dtypes[col] = "float"

        # read data
        data = pd.read_csv(data_filepath, sep=";", dtype=dtypes)
        data[timestamp_col] = pd.to_datetime(data[timestamp_col])

        # split into train and test using temporal split
        grouped = data.groupby(case_id_col)
        start_timestamps = grouped[timestamp_col].min().reset_index()
        start_timestamps.sort_values(timestamp_col, ascending=1, inplace=True)
        train_ids = list(start_timestamps[case_id_col])[:int(train_ratio*len(start_timestamps))]
        train = data[data[case_id_col].isin(train_ids)]
        test = data[~data[case_id_col].isin(train_ids)]

        grouped_train = train.sort_values(timestamp_col, ascending=True).groupby(case_id_col)
        grouped_test = test.sort_values(timestamp_col, ascending=True).groupby(case_id_col)
        
        test_case_lengths = grouped_test.size()

        # generate prefix data (each possible prefix becomes a trace)
        train_prefixes = grouped_train.head(prefix_lengths[0])
        for nr_events in prefix_lengths[1:]:
            tmp = grouped_train.head(nr_events)
            tmp[case_id_col] = tmp[case_id_col].apply(lambda x: "%s_%s"%(x, nr_events))
            train_prefixes = pd.concat([train_prefixes, tmp], axis=0)
        
        
        for method_name, methods in methods_dict.items():
            
            cls = RandomForestClassifier(n_estimators=rf_n_estimators, random_state=random_state)
            feature_combiner = FeatureUnion([(method, init_encoder(method)) for method in methods])
            pipeline = Pipeline([('encoder', feature_combiner), ('cls', cls)])
            
            # fit pipeline
            train_y = train_prefixes.groupby(case_id_col).first()[label_col]
            pipeline.fit(train_prefixes, train_y)
            preds_pos_label_idx = np.where(pipeline.named_steps["cls"].classes_ == pos_label)[0][0] 
        
            # test separately for each prefix length
            for nr_events in prefix_lengths:
                
                # select only cases that are at least of length nr_events
                relevant_case_ids = test_case_lengths.index[test_case_lengths >= nr_events]
                relevant_grouped_test = test[test[case_id_col].isin(relevant_case_ids)].sort_values(timestamp_col, ascending=True).groupby(case_id_col)
                test_y = [1 if label==pos_label else 0 for label in relevant_grouped_test.first()[label_col]]
            
                preds = pipeline.predict_proba(relevant_grouped_test.head(nr_events))

                auc = roc_auc_score(test_y, preds[:,preds_pos_label_idx])
                prec, rec, fscore, _ = precision_recall_fscore_support(test_y, [0 if pred < 0.5 else 1 for pred in preds[:,preds_pos_label_idx]], average="binary")

                fout.write("%s;%s;%s;%s;%s\n"%(dataset_name, method_name, nr_events, "auc", auc))
                fout.write("%s;%s;%s;%s;%s\n"%(dataset_name, method_name, nr_events, "precision", prec))
                fout.write("%s;%s;%s;%s;%s\n"%(dataset_name, method_name, nr_events, "recall", rec))
                fout.write("%s;%s;%s;%s;%s\n"%(dataset_name, method_name, nr_events, "fscore", fscore))
                