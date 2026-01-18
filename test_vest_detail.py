from app import create_app, db
from app.models.vest_event import VestEvent
from app.models.grant import Grant
from app.models.stock_sale import StockSale, ISOExercise

app = create_app()

with app.app_context():
    try:
        vest = VestEvent.query.first()
        if vest:
            print(f"Testing vest ID: {vest.id}")
            
            sales = StockSale.query.filter_by(vest_event_id=vest.id).all()
            exercises = ISOExercise.query.filter_by(vest_event_id=vest.id).all()
            
            print(f"Sales: {len(sales)}")
            print(f"Exercises: {len(exercises)}")
            
            total_sold = sum(s.shares_sold for s in sales)
            total_exercised = sum(e.shares_exercised for e in exercises)
            
            print(f"Total sold: {total_sold}")
            print(f"Total exercised: {total_exercised}")
            print(f"Shares received: {vest.shares_received}")
            
            remaining = vest.shares_received - total_sold - total_exercised
            print(f"Remaining: {remaining}")
            
            print("✅ All calculations successful")
        else:
            print("No vests found")
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
