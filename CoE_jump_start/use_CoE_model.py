import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
kit_dir = os.path.abspath(os.path.join(current_dir, ".."))
repo_dir = os.path.abspath(os.path.join(kit_dir, ".."))

sys.path.append(kit_dir)
sys.path.append(repo_dir)

import logging
from typing import Dict, Any, Optional, Union
from dotenv import load_dotenv
from langchain.prompts import PromptTemplate, ChatPromptTemplate
from langchain_community.document_loaders import WebBaseLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.chains.combine_documents.stuff import create_stuff_documents_chain
from langchain.chains import create_retrieval_chain
from utils.sambanova_endpoint import SambaStudio, SambaStudioEmbeddings, Sambaverse
import yaml
import json
import requests

# Use embeddings As Part of Langchain
from langchain_community.vectorstores import Chroma

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(current_dir, "config.yaml")

with open(CONFIG_PATH, "r") as yaml_file:
    config = yaml.safe_load(yaml_file)
api_info = config["api"]
llm_info = config["llm"]
retrieval_info = config["retrieval"]

# Load environment variables from .env file
load_dotenv(os.path.join(current_dir, ".env"))


def get_expert_val(res: Union[Dict[str, Any], str]) -> str:
    """
    Extract the expert value from the API response.

    Args:
        res (Union[Dict[str, Any], str]): The API response, either as a dictionary or a string.

    Returns:
        str: The expert value or "Generalist" if not found.
    """
    supported_experts_map = config["supported_experts_map"]
    supported_experts = list(supported_experts_map.keys())

    if isinstance(res, str):
        # If res is a string, treat it as the data directly
        data = res.strip().lower()
    elif isinstance(res, dict):
        # If res is a dictionary, extract data as before
        if not res or not res.get("data") or not res["data"]:
            return "Generalist"
        data = (res["data"][0].get("completion", "") or "").strip().lower()
    else:
        # If res is neither a string nor a dictionary, return "Generalist"
        return "Generalist"

    expert = next((x for x in supported_experts if x in data), "Generalist")
    return supported_experts_map.get(expert, "Generalist")

def get_expert(
    input_text: str,
    do_sample: bool = False,
    max_tokens_to_generate: int = 500,
    repetition_penalty: float = 1.0,
    temperature: float = 0.7,
    top_k: int = 50,
    top_p: float = 1.0,
    select_expert: str = "Meta-Llama-3-70B-Instruct",
    use_requests: bool = False,
    use_wrapper: bool = False,
) -> Dict[str, Any]:
    """
    Classifies the given input text into one of the predefined categories.

    Args:
        input_text (str): The input text to classify.
        do_sample (bool): Whether to sample from the model's output distribution. Default is False.
        max_tokens_to_generate (int): The maximum number of tokens to generate. Default is 500.
        repetition_penalty (float): The penalty for repeating tokens. Default is 1.0.
        temperature (float): The temperature for sampling. Default is 0.7.
        top_k (int): The number of top most likely tokens to consider. Default is 50.
        top_p (float): The cumulative probability threshold for top-p sampling. Default is 1.0.
        select_expert (str): The name of the expert model to use. Default is "llama-2-7b-chat-hf".
        use_requests (bool): Whether to use the requests library instead of SNSDK. Default is False.
        use_wrapper (bool): Whether to use the SambaStudio wrapper with langchain. Default is False.

    Returns:
        Dict[str, Any]: The response from the model.
    """
    select_expert = config["coe_name_map"]["Generalist"]
    prompt = config["expert_prompt"].format(input=input_text)

    inputs = json.dumps(
        {
            "conversation_id": "sambaverse-conversation-id",
            "messages": [{"message_id": 0, "role": "user", "content": input_text}],
            "prompt": prompt,
        }
    )


    tuning_params = {
        "do_sample": {"type": "bool", "value": str(do_sample).lower()},
        "max_tokens_to_generate": {"type": "int", "value": str(max_tokens_to_generate)},
        "repetition_penalty": {"type": "float", "value": str(repetition_penalty)},
        "temperature": {"type": "float", "value": str(temperature)},
        "top_k": {"type": "int", "value": str(top_k)},
        "top_p": {"type": "float", "value": str(top_p)},
        "select_expert": {"type": "str", "value": select_expert},
        "process_prompt": {"type": "bool", "value": "false"},
    }

    if use_wrapper:
        llm = SambaStudio(
            streaming=True,
            model_kwargs={
                "do_sample": do_sample,
                "temperature": temperature,
                "max_tokens_to_generate": max_tokens_to_generate,
                "select_expert": select_expert,
                "process_prompt": False,
            }
        )
        chat_prompt = ChatPromptTemplate.from_template(config["expert_prompt"])
        return llm.invoke(chat_prompt.format_prompt(input=input_text).to_string())
    elif use_requests:
        url = "{}/api/predict/generic/{}/{}"
        headers = {"Content-Type": "application/json", "key": os.getenv("SAMBASTUDIO_API_KEY")}
        data = {
            "instances": [inputs],
            "params": tuning_params,
        }

        response = requests.post(
            url.format(os.getenv("SAMBASTUDIO_BASE_URL"), os.getenv("SAMBASTUDIO_PROJECT_ID"), os.getenv("SAMBASTUDIO_ENDPOINT_ID")),
            headers=headers,
            json=data,
        )
        response.raise_for_status()
        return response.json()
    else:
        from snsdk import SnSdk

        sdk = SnSdk(os.getenv("SAMBASTUDIO_BASE_URL"), "endpoint_secret")
        return sdk.nlp_predict(
            os.getenv("SAMBASTUDIO_PROJECT_ID"),
            os.getenv("SAMBASTUDIO_ENDPOINT_ID"),
            os.getenv("SAMBASTUDIO_API_KEY"),
            inputs,
            json.dumps(tuning_params),
        )

def main() -> None:
    """Main function to run the script."""
    # Create a SambaStudioEmbeddings object
    snsdk_model = SambaStudioEmbeddings()
    embeddings = snsdk_model

    # Load documents from the specified URL
    loader = WebBaseLoader("https://docs.smith.langchain.com")
    docs = loader.load()

    # Split the documents into chunks
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=retrieval_info["chunk_size"],
        chunk_overlap=retrieval_info["chunk_overlap"],
    )
    documents = text_splitter.split_documents(docs)

    # Create a vector database using Chroma
    vector = Chroma.from_documents(documents, embeddings)

    # Define the prompt template
    prompt = ChatPromptTemplate.from_template(
        """Answer the following question based only on the provided context:

        <context>
        {context}
        </context>

        Question: {input}"""
    )

    # Set up the language model based on the API configuration
    llm: Optional[SambaStudio] = None

    if api_info == "sambaverse":
        llm = Sambaverse(
            sambaverse_model_name=llm_info["sambaverse_model_name"],
            sambaverse_api_key=os.getenv("SAMBAVERSE_API_KEY"),
            model_kwargs={
                "do_sample": False,
                "max_tokens_to_generate": llm_info["max_tokens_to_generate"],
                "temperature": llm_info["temperature"],
                "process_prompt": True,
                "select_expert": llm_info["samabaverse_select_expert"],
            },
        )
    elif api_info == "sambastudio":
        llm = SambaStudio(
            streaming=True,
            model_kwargs={
                "do_sample": True,
                "temperature": llm_info["temperature"],
                "max_tokens_to_generate": llm_info["max_tokens_to_generate"],
                "select_expert": llm_info["samabaverse_select_expert"],
                "process_prompt": False,
            }
        )

    user_query = "What is langsmith"

    if llm_info["coe_routing"]:
        # Get the expert by calling SambaStudio with a custom prompt workflow
        expert_response = get_expert(user_query, use_requests=True)
        logger.info(f"Expert response: {expert_response}")

        # Extract the expert name from the response
        expert = get_expert_val(expert_response)
        logger.info(f"Expert: {expert}")

        # Look up the model name based on the expert
        named_expert = config["coe_name_map"][expert]
        logger.info(f"Named expert: {named_expert}")

        llm = SambaStudio(
            streaming=True,
            model_kwargs={
                "do_sample": True,
                "temperature": llm_info["temperature"],
                "max_tokens_to_generate": llm_info["max_tokens_to_generate"],
                "select_expert": named_expert,
                "process_prompt": False,
            }
        )

    if llm is None:
        raise ValueError("Invalid API configuration")

    # Create the document chain and retrieval chain
    document_chain = create_stuff_documents_chain(llm, prompt)
    retriever = vector.as_retriever()
    retrieval_chain = create_retrieval_chain(retriever, document_chain)

    # Invoke the retrieval chain with the user query
    response = retrieval_chain.invoke({"input": user_query})
    logger.info(f"Response: {response['answer']}")

if __name__ == "__main__":
    main()