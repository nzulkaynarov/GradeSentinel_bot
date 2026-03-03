from src.database_manager import add_family, add_parent, add_student, link_family

def seed():
    print("Seeding test data...")
    # Add a test family
    fam_id = add_family("Test Family")
    
    # Add a test parent (change phone if testing actual bot)
    parent_id = add_parent("Test Parent", "79991234567", is_admin=True)
    
    # Add a test student with a known public sheet (or user provided one)
    # For now, using a placeholder. User can provide their own.
    student_id = add_student("Test Student", "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms") # Sample public sheet
    
    # Link them
    link_family(fam_id, parent_id, student_id)
    print("Seeding complete.")

if __name__ == "__main__":
    seed()
