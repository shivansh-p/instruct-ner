import re
import os
import argparse
import torch
import numpy as np
import pandas as pd
from tqdm import tqdm
from transformers import AutoTokenizer, AutoModelForCausalLM, T5ForConditionalGeneration, GenerationConfig
from peft import PeftConfig, PeftModel
from utils.rudrec.rudrec_reader import create_train_test_instruct_datasets
from utils.nerel_bio.nerel_reader import create_instruct_dataset

# from utils.rudrec.rudrec_utis import ENTITY_TYPES
from metric import extract_classes


def batch(iterable, n=4):
    l = len(iterable)
    for ndx in range(0, l, n):
        yield iterable[ndx:min(ndx + n, l)]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset_name", default='rudrec', type=str, help='name of dataset')
    parser.add_argument("--data_path", default='data/rudrec/rudrec_annotated.json', type=str, help='train file_path')
    parser.add_argument("--model_type", default='llama', type=str, help='model type')
    parser.add_argument("--model_name", default='poteminr/llama2-rudrec', type=str, help='model name from hf')
    parser.add_argument("--prediction_path", default='prediction.json', type=str, help='path for saving prediction')
    parser.add_argument("--max_instances", default=-1, type=int, help='max number of instruction')
    arguments = parser.parse_args()

    model_name = arguments.model_name
    generation_config = GenerationConfig.from_pretrained(model_name)
    
    peft_config = PeftConfig.from_pretrained(arguments.model_name)
    base_model_name = peft_config.base_model_name_or_path
    
    models = {'llama': AutoModelForCausalLM, 't5': T5ForConditionalGeneration}
    
    model = models[arguments.model_type].from_pretrained(
        base_model_name,
        load_in_8bit=True,
        device_map='auto'
    )
    
    model = PeftModel.from_pretrained(model, model_name)
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    
    model.eval()
    model = torch.compile(model)
    
    if arguments.dataset_name == 'rudrec': 
        from utils.rudrec.rudrec_utis import ENTITY_TYPES
        _, test_dataset = create_train_test_instruct_datasets(arguments.data_path)
        if arguments.max_instances != -1 and arguments.max_instances < len(test_dataset):
            test_dataset = test_dataset[:arguments.max_instances]
    else:
        from utils.nerel_bio.nerel_bio_utils import ENTITY_TYPES
        test_path = os.path.join(arguments.data_path, 'test')
        test_dataset = create_instruct_dataset(test_path, max_instances=arguments.max_instances)

    
    extracted_list = []
    target_list = []
    instruction_ids = []
    sources = []
    
    for instruction in tqdm(test_dataset):
        target_list.append(instruction['raw_entities'])
        instruction_ids.append(instruction['id'])
        sources.append(instruction['source'])
        
    target_list = list(batch(target_list))
    instruction_ids = list(batch(instruction_ids))    
    sources = list(batch(sources))
    
    for source in tqdm(sources):
        input_ids = tokenizer(source, return_tensors="pt", padding=True)["input_ids"].cuda()
        with torch.no_grad():
            generation_output = model.generate(
                input_ids=input_ids,
                generation_config=generation_config,
                return_dict_in_generate=True,
                eos_token_id=tokenizer.eos_token_id,
                early_stopping=True,
            )
        for s in generation_output.sequences:
            string_output = tokenizer.decode(s, skip_special_tokens=True)
            extracted_list.append(extract_classes(string_output, ENTITY_TYPES))
    
    pd.DataFrame({
        'id': np.concatenate(instruction_ids), 
        'extracted': extracted_list,
        'target': np.concatenate(target_list)
    }).to_json(arguments.prediction_path)