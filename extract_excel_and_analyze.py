import os
import pandas as pd
from flask import Flask, request, render_template
from openai import AzureOpenAI
from azure.cosmos import CosmosClient, PartitionKey
import re
import json

app = Flask(__name__)

# Cosmos DB Configuration
COSMOS_ENDPOINT = " "
COSMOS_KEY = " "
DATABASE_NAME = " "
CONTAINER_NAME = " "

 

# 📍 Local KPI file path
KPI_FILE_PATH = os.path.join(os.getcwd(), "KPI", "CorePriority.xlsx")

@app.route('/')
def index():
    return render_template("index.html")

@app.route('/analyze', methods=['POST'])
def analyze():
    if 'file' not in request.files:
        return "❌ No file uploaded"

    file = request.files['file']
    if file.filename == '':
        return "❌ No file selected"

    try:
      
# Load KPI file (local)
        kpi_df = pd.read_excel(KPI_FILE_PATH, sheet_name=0, engine="openpyxl")
        print("KPI Columns:", list(kpi_df.columns))

        # Build KPI text dynamically from all columns
        kpi_text = "\n\n".join([
            "\n".join([f"{col}: {row[col]}" for col in kpi_df.columns])
            for _, row in kpi_df.iterrows()
        ])


        # Load uploaded activity file
        input_df = pd.read_excel(file, sheet_name=0, engine="openpyxl")
        if 'Activities' not in input_df.columns:
            return "❌ Uploaded file must contain 'Activities' column"      
     

        activities = input_df['Activities'].dropna().astype(str).tolist()

        # 🧠 GPT Prompt
        prompt = f"""
You are an expert data analyst.

Below is a list of organizational KPIs:

{kpi_text}

Now here is a list of activities submitted by a user:

{chr(10).join(f"- {a}" for a in activities)}

For each activity, map it to the most relevant GOAL from the KPI list. If no match is found, say "No match".

Return the result as a json with   
KPI , Activity , SucessMeasure ,Reason

 
"""
        print("🧠 Sending prompt to Azure OpenAI")
        # Call Azure OpenAI
        response = client.chat.completions.create(
            model="gpt-4",  # Replace with your actual deployment name if needed
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt}
            ]
        )

        
        raw_result = response.choices[0].message.content

        # 🔎 Extract JSON block from GPT output
        match = re.search(r"```json\s*(.*?)\s*```", raw_result, re.DOTALL)
        if match:
            json_text = match.group(1)
        else:
            json_text = raw_result.strip()

        try:
            activities_json = json.loads(json_text)
        except json.JSONDecodeError as e:
            return f"❌ Failed to parse JSON from GPT output: {e}"

        items_with_embeddings = []

        # Initialize Cosmos DB client
        cosmos_client = CosmosClient(COSMOS_ENDPOINT, COSMOS_KEY)

        # Create or get database + container
        database = cosmos_client.create_database_if_not_exists(id=DATABASE_NAME)
        container = database.create_container_if_not_exists(
            id=CONTAINER_NAME,
            partition_key=PartitionKey(path="/Activity"),
            offer_throughput=400
        )
        
        for entry in activities_json:
            # Create embedding for each entry
            embedding_response = client.embeddings.create(
                model="text-embedding-ada-002",
                input=json.dumps(entry)
            )
            embedding_vector = embedding_response.data[0].embedding

            # Append entry with embedding
            items_with_embeddings.append({
                "KPI": entry.get("KPI"),
                "Activity": entry.get("Activity"),
                "SuccessMeasure": entry.get("SuccessMeasure"),
                "Reason": entry.get("Reason"),
                "Embedding": embedding_vector
            })

        # Final single document
        item = {
            "id": file.filename,  # e.g. "Activities.xlsx"
            "Results": items_with_embeddings
        }

        # Upsert into Cosmos DB
        container.upsert_item(item)

        return f"✅ Analysis complete and stored in Cosmos DB.<br><br>{json.dumps(activities_json, indent=2)}"


    except Exception as e:
        return f"❌ Error while analyzing file: {e}"

if __name__ == "__main__":
    app.run(debug=True)
