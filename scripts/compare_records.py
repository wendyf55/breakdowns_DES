#takes any two csvs in data/ and compares them
# comparison is unique because it allows you to search multiple columns in multiple ways for comparing
# similar to the orchestration pipeline, we get a seed list of candidates
# and move them through workflows
# to start, the only thing I want in compare records is this:
# seearches all columns of specify database for some kind of lookup:
# for MO, we will use "beginning with MUOB", and has x number of digits after that
# once found, we create a new column in the dataframe that's 'MUOB Numbers" and has all of those numbers
# in it. Then we do the same thing on the specify dataset, and compare the two "MUOB Numbers" columns
3# and counts the number of numbers that are present in both, present in specify, present in MO,
# and columns that have neither. 
# this is ONE method of comparison we'll use for just MO and specify count matches method, but later
# we'll have to abstract this method and try lots of different things

